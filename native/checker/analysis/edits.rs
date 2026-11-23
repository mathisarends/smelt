//! Source edits that rule fixes share: lines, name uses and imports.
use super::dump::joined_constants;
use crate::checker::source::{ParsedSource, SourceText};
use regex::Regex;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Alias, Expr, ModModule, Stmt};
use ruff_text_size::{Ranged, TextRange};
use serde_json::{Value, json};
use std::sync::LazyLock;

static WORD: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[\p{L}\p{N}_]$").expect("the word class is valid"));

/// A `Fix` as JSON.
pub fn fix(applicability: &str, description: &str, edits: Vec<Value>) -> Value {
    json!({
        "applicability": applicability,
        "description": description,
        "edits": edits,
    })
}

/// `str.isspace` for one character, which also covers U+001C to U+001F.
pub fn is_python_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

/// `str.strip()`.
pub fn python_strip(text: &str) -> &str {
    text.trim_matches(is_python_space)
}

/// Whether `re`'s `\w` matches `character`.
pub fn is_word(character: char) -> bool {
    if character.is_ascii() {
        return character.is_ascii_alphanumeric() || character == '_';
    }
    WORD.is_match(character.encode_utf8(&mut [0; 4]))
}

/// Whether `\b` holds between the two characters around a position.
pub fn is_word_boundary(before: Option<char>, after: Option<char>) -> bool {
    before.is_some_and(is_word) != after.is_some_and(is_word)
}

/// `re.search(rf"\b{re.escape(word)}\b", text)`.
fn contains_word(text: &str, word: &str) -> bool {
    let (Some(first), Some(last)) = (word.chars().next(), word.chars().next_back()) else {
        return false;
    };
    let mut from = 0;
    while let Some(found) = text[from..].find(word) {
        let start = from + found;
        let end = start + word.len();
        if is_word_boundary(text[..start].chars().next_back(), Some(first))
            && is_word_boundary(Some(last), text[end..].chars().next())
        {
            return true;
        }
        from = start + first.len_utf8();
    }
    false
}

/// A subtree a name search leaves out.
#[derive(Clone, Copy)]
pub enum Skipped<'a> {
    Statement(&'a Stmt),
    Expression(&'a Expr),
}

/// Whether `name` is read anywhere outside `ignore`, also inside strings.
pub fn name_used(parsed: &ParsedSource, name: &str, ignore: &[Skipped<'_>]) -> bool {
    let mut search = NameSearch {
        source: parsed.text.text.as_str(),
        name,
        ignore,
        found: false,
    };
    search.visit_body(&parsed.module().body);
    search.found
}

struct NameSearch<'b, 'c> {
    source: &'b str,
    name: &'b str,
    ignore: &'b [Skipped<'c>],
    found: bool,
}

impl NameSearch<'_, '_> {
    fn skips_statement(&self, statement: &Stmt) -> bool {
        self.ignore
            .iter()
            .any(|item| matches!(item, Skipped::Statement(node) if std::ptr::eq(*node, statement)))
    }

    fn skips_expression(&self, expression: &Expr) -> bool {
        self.ignore.iter().any(
            |item| matches!(item, Skipped::Expression(node) if std::ptr::eq(*node, expression)),
        )
    }
}

impl<'a> Visitor<'a> for NameSearch<'_, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if self.found || self.skips_statement(statement) {
            return;
        }
        let names = match statement {
            Stmt::Global(node) => node.names.as_slice(),
            Stmt::Nonlocal(node) => node.names.as_slice(),
            _ => &[],
        };
        if names.iter().any(|item| item.as_str() == self.name) {
            self.found = true;
            return;
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if self.found || self.skips_expression(expression) {
            return;
        }
        self.found = match expression {
            Expr::Name(node) => node.id.as_str() == self.name,
            Expr::StringLiteral(node) => contains_word(node.value.to_str(), self.name),
            Expr::FString(_) | Expr::TString(_) => joined_constants(self.source, expression)
                .iter()
                .any(|constant| contains_word(constant, self.name)),
            _ => false,
        };
        if !self.found {
            visitor::walk_expr(self, expression);
        }
    }
}

fn aliases_of(statement: &Stmt) -> &[Alias] {
    match statement {
        Stmt::Import(node) => &node.names,
        Stmt::ImportFrom(node) => &node.names,
        _ => &[],
    }
}

/// The module-level import statement and alias that bind `local`.
pub fn top_level_import<'a>(module: &'a ModModule, local: &str) -> Option<(&'a Stmt, &'a Alias)> {
    for statement in &module.body {
        for alias in aliases_of(statement) {
            let bound = match &alias.asname {
                Some(name) => name.as_str(),
                None => alias.name.split('.').next().unwrap_or_default(),
            };
            if bound == local {
                return Some((statement, alias));
            }
        }
    }
    None
}

/// Extend a module-level `from module import ...` or add one after the imports.
pub fn add_from_import(parsed: &ParsedSource, target: &str, name: &str) -> Value {
    let text = &parsed.text;
    for statement in &parsed.module().body {
        if let Stmt::ImportFrom(node) = statement
            && node.level == 0
            && node
                .module
                .as_ref()
                .is_some_and(|item| item.as_str() == target)
            && node.names[0].name.as_str() != "*"
            && let Some(last) = node.names.last()
        {
            return text.insert(usize::from(last.end()), &format!(", {name}"));
        }
    }
    let offset = if target == "__future__" {
        future_insertion(parsed)
    } else {
        import_insertion(parsed)
    };
    let following = text.text[offset..].split('\n').next().unwrap_or_default();
    // A blank line separates the new import from code that follows directly.
    let gap = if !python_strip(following).is_empty()
        && !following.starts_with("import ")
        && !following.starts_with("from ")
    {
        "\n"
    } else {
        ""
    };
    text.insert_line(offset, &format!("from {target} import {name}{gap}"))
}

/// Drop `aliases`, or the whole statement when none remain.
///
/// Only statements that fill their lines are removed whole, so no block is left
/// empty. Comments between aliases would be lost, so they leave the import.
pub fn remove_aliases(text: &SourceText, statement: &Stmt, aliases: &[&Alias]) -> Option<Value> {
    let names = aliases_of(statement);
    let kept: Vec<&Alias> = names
        .iter()
        .filter(|alias| !aliases.iter().any(|item| std::ptr::eq(*item, *alias)))
        .collect();
    if kept.is_empty() {
        return text
            .stands_alone(statement.range())
            .then(|| text.delete_lines(statement.range()));
    }
    let start = usize::from(names.first()?.start());
    let end = usize::from(names.last()?.end());
    if text.text[start..end].contains('#') || kept.len() == names.len() {
        return None;
    }
    let listed: Vec<&str> = kept
        .iter()
        .map(|alias| text.segment(alias.range()))
        .collect();
    Some(text.edit(start, end, &listed.join(", ")))
}

/// Where a new import line goes: after the leading imports or the docstring.
pub fn import_insertion(parsed: &ParsedSource) -> usize {
    let text = &parsed.text;
    let body = &parsed.module().body;
    let mut anchor = None;
    for (index, statement) in body.iter().enumerate() {
        if (index == 0 && is_docstring(statement))
            || matches!(statement, Stmt::Import(_) | Stmt::ImportFrom(_))
        {
            anchor = Some(statement);
        } else if !guarded_imports(statement) {
            break;
        }
    }
    if let Some(anchor) = anchor {
        return text.line_end(text.line_at(usize::from(anchor.end())));
    }
    body.first()
        .map_or(text.text.len(), |first| first_line(parsed, first))
}

/// Where a future import goes: directly after the docstring, before any code.
fn future_insertion(parsed: &ParsedSource) -> usize {
    let text = &parsed.text;
    match parsed.module().body.first() {
        Some(first) if is_docstring(first) => text.line_end(text.line_at(usize::from(first.end()))),
        Some(first) => first_line(parsed, first),
        None => 0,
    }
}

fn first_line(parsed: &ParsedSource, statement: &Stmt) -> usize {
    let text = &parsed.text;
    text.line_start(text.line_at(usize::from(parsed.statement_start(statement))))
}

pub fn is_docstring(statement: &Stmt) -> bool {
    matches!(statement, Stmt::Expr(node) if node.value.is_string_literal_expr())
}

/// Whether a statement only imports, possibly inside `if` or `try` blocks.
pub fn guarded_imports(statement: &Stmt) -> bool {
    match statement {
        Stmt::If(node) => node
            .body
            .iter()
            .chain(
                node.elif_else_clauses
                    .iter()
                    .flat_map(|clause| &clause.body),
            )
            .all(guarded_imports),
        Stmt::Try(node) if !node.is_star => {
            node.body
                .iter()
                .chain(&node.orelse)
                .chain(&node.finalbody)
                .all(guarded_imports)
                && node.handlers.iter().all(|handler| {
                    let handler = handler
                        .as_except_handler()
                        .expect("a handler is an except clause");
                    handler
                        .body
                        .iter()
                        .all(|item| guarded_imports(item) || matches!(item, Stmt::Pass(_)))
                })
        }
        Stmt::Import(_) | Stmt::ImportFrom(_) => true,
        _ => false,
    }
}

impl SourceText {
    pub fn segment(&self, range: TextRange) -> &str {
        &self.text[usize::from(range.start())..usize::from(range.end())]
    }

    pub fn replace(&self, range: TextRange, replacement: &str) -> Value {
        self.edit(
            usize::from(range.start()),
            usize::from(range.end()),
            replacement,
        )
    }

    pub fn insert(&self, offset: usize, insertion: &str) -> Value {
        self.edit(offset, offset, insertion)
    }

    /// Insert `line` as a whole line at `offset`, the start of a line.
    pub fn insert_line(&self, offset: usize, line: &str) -> Value {
        let ends_line = self.text.is_empty() || self.text.ends_with('\n');
        let lead = if offset == self.text.len() && !ends_line {
            "\n"
        } else {
            ""
        };
        self.insert(offset, &format!("{lead}{line}\n"))
    }

    /// Whether a statement fills its lines, with at most a trailing comment.
    pub fn stands_alone(&self, range: TextRange) -> bool {
        let start = usize::from(range.start());
        let end = usize::from(range.end());
        let before = &self.text[self.line_start(self.line_at(start))..start];
        let after = python_strip(&self.text[end..self.line_end(self.line_at(end))]);
        python_strip(before).is_empty() && (after.is_empty() || after.starts_with('#'))
    }

    /// The end of the line that contains `offset`, its newline included.
    pub fn line_end_at(&self, offset: usize) -> usize {
        self.line_end(self.line_at(offset))
    }

    /// The start of the line at `offset`, with comment lines directly above.
    pub fn block_start(&self, offset: usize) -> usize {
        let mut start = self.line_start(self.line_at(offset));
        while start > 0 {
            let previous = self.line_start(self.line_at(start - 1));
            if !self.text[previous..start]
                .trim_start_matches(is_python_space)
                .starts_with('#')
            {
                break;
            }
            start = previous;
        }
        start
    }

    /// Remove the whole lines `range` occupies, the last newline included.
    pub fn delete_lines(&self, range: TextRange) -> Value {
        let start = self.line_start(self.line_at(usize::from(range.start())));
        let end = self.line_end(self.line_at(usize::from(range.end())));
        self.edit(start, end, "")
    }
}
