//! LAY012: `layout/redundant-comment`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::dotted_name;
use crate::checker::analysis::comments::Comments;
use crate::checker::violation::Violation;
use regex::Regex;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt, StmtClassDef};
use ruff_text_size::{Ranged, TextSize};
use serde_json::json;
use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

/// Articles, prepositions and filler verbs that add nothing to a restated name.
const STOP_WORDS: [&str; 46] = [
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "class", "constant", "for", "from",
    "function", "get", "gets", "getting", "helper", "helpers", "here", "in", "instance", "into",
    "is", "it", "its", "method", "module", "new", "object", "of", "on", "or", "package", "return",
    "returned", "returns", "that", "the", "these", "this", "those", "to", "value", "variable",
    "with",
];

/// Comments that are not prose about the next statement: tool directives, license headers,
/// tracked work, section dividers and links.
static NOT_PROSE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)
        \#\s*(?:noqa|type:|pyright:|pylint:|pragma:|smelt:|fmt:|mypy:|ruff:|isort:|flake8:)
        | \b(?:copyright|license|spdx|todo|fixme|xxx|hack)\b
        | [-=*\#~_]{3,}
        | ://",
    )
    .expect("the pattern is valid")
});

const HINT: &str =
    "Say why the code exists or what is non-obvious about it, or remove the comment.";

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let comments = Comments::scan(input.parsed);
    let mut collector = Documented {
        found: Vec::new(),
        interface: false,
    };
    collector.visit_body(&input.parsed.module().body);
    let mut found = Vec::new();
    let module_name = input
        .source
        .module
        .rsplit('.')
        .next()
        .unwrap_or_default()
        .to_owned();
    if let Some((node, text)) = docstring(&input.parsed.module().body) {
        let required = input
            .contracts
            .policy_for(&input.source.module, "module_docstrings")
            .is_some_and(|contract| contract.value.as_str() == Some("require"));
        let rule = required.then_some("layout/module-docstring-convention");
        found.extend(restating_docstring(input, (node, text), &module_name, rule));
    }
    for item in collector.found {
        if let Some(docstring) = docstring(item.body) {
            let rule = item
                .interface
                .then_some("idioms/missing-interface-docstring");
            found.extend(restating_docstring(input, docstring, item.name, rule));
        }
        let line = input.place(item.start).0;
        let mut block = comments.block_above(line);
        block.reverse();
        let Some(&first) = block.first() else {
            continue;
        };
        let text = block
            .iter()
            .filter_map(|line| comments.text(*line))
            .collect::<Vec<_>>()
            .join("\n");
        let offset = comments.offset(first).unwrap_or_default();
        let column = input
            .place(TextSize::try_from(offset).unwrap_or_default())
            .1;
        let Some(words) = restated(&text, item.name) else {
            continue;
        };
        found.push(input.violation(Finding {
            message: format!("comment only repeats the name `{}`", item.name),
            line: first,
            column,
            end_line: Some(line - 1),
            end_column: None,
            expected: json!({
                "kind": "comment",
                "name": item.name,
                "words": words,
                "required_docstring": null,
                "automatic_rewrite": false,
            }),
            hint: format!("This comment only repeats the name `{}`. {HINT}", item.name),
            fix: None,
        }));
    }
    found.sort_by_key(|violation| (violation.line, violation.column));
    found
}

fn restating_docstring(
    input: &RuleInput<'_>,
    docstring: (&Stmt, &str),
    name: &str,
    required_by: Option<&str>,
) -> Option<Violation> {
    let (node, text) = docstring;
    let words = restated(text, name)?;
    let (line, column) = input.place(node.start());
    let (end_line, end_column) = input.place(node.end());
    let hint = match required_by {
        Some(rule) => format!(
            "This docstring only repeats the name `{name}`. {rule} requires a docstring here: say what is non-obvious instead of removing it."
        ),
        None => format!(
            "This docstring only repeats the name `{name}`. Say what is non-obvious about it, or remove the docstring."
        ),
    };
    Some(input.violation(Finding {
        message: format!("docstring only repeats the name `{name}`"),
        line,
        column,
        end_line: Some(end_line),
        end_column: Some(end_column),
        expected: json!({
            "kind": "docstring",
            "name": name,
            "words": words,
            "required_docstring": required_by,
            "automatic_rewrite": false,
        }),
        hint,
        fix: None,
    }))
}

/// The words a comment shares with a name, when it says nothing else.
fn restated(text: &str, name: &str) -> Option<Vec<String>> {
    if NOT_PROSE.is_match(text) {
        return None;
    }
    let stop: HashSet<&str> = STOP_WORDS.into_iter().collect();
    let named: HashSet<String> = words(name).iter().map(|word| stem(word)).collect();
    let said: BTreeSet<String> = words(text)
        .into_iter()
        .filter(|word| !stop.contains(word.as_str()))
        .map(|word| stem(&word))
        .collect();
    (!said.is_empty() && said.iter().all(|word| named.contains(word)))
        .then(|| said.into_iter().collect())
}

/// Lowercase words, split at punctuation, underscores and CamelCase humps.
fn words(text: &str) -> Vec<String> {
    let mut found = Vec::new();
    for part in text.split(|character: char| !character.is_alphanumeric()) {
        let characters: Vec<char> = part.chars().collect();
        let mut current = String::new();
        for (index, character) in characters.iter().enumerate() {
            let previous = index.checked_sub(1).map(|at| characters[at]);
            let next = characters.get(index + 1);
            let hump = character.is_uppercase()
                && previous.is_some_and(|before| {
                    before.is_lowercase()
                        || (before.is_uppercase() && next.is_some_and(|after| after.is_lowercase()))
                });
            if hump && !current.is_empty() {
                found.push(std::mem::take(&mut current));
            }
            current.extend(character.to_lowercase());
        }
        if !current.is_empty() {
            found.push(current);
        }
    }
    found
}

/// A light plural and third-person stem: `users` and `creates` lose their `s`.
fn stem(word: &str) -> String {
    if word.len() > 4 && word.ends_with("ies") {
        return format!("{}y", &word[..word.len() - 3]);
    }
    for suffix in ["sses", "xes", "ches", "shes"] {
        if word.ends_with(suffix) {
            return word[..word.len() - 2].to_owned();
        }
    }
    if word.len() > 3 && word.ends_with('s') && !word.ends_with("ss") && !word.ends_with("us") {
        return word[..word.len() - 1].to_owned();
    }
    word.to_owned()
}

fn docstring(body: &[Stmt]) -> Option<(&Stmt, &str)> {
    let first = body.first()?;
    let Stmt::Expr(node) = first else {
        return None;
    };
    let Expr::StringLiteral(text) = node.value.as_ref() else {
        return None;
    };
    Some((first, text.value.to_str()))
}

/// A definition or simple assignment a comment above can describe.
struct Item<'a> {
    name: &'a str,
    start: TextSize,
    body: &'a [Stmt],
    interface: bool,
}

struct Documented<'a> {
    found: Vec<Item<'a>>,
    /// Whether the enclosing class is a `Protocol`.
    interface: bool,
}

impl<'a> Visitor<'a> for Documented<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => {
                let abstract_ = node.decorator_list.iter().any(|decorator| {
                    dotted_name(&decorator.expression)
                        .is_some_and(|name| name.rsplit('.').next() == Some("abstractmethod"))
                });
                self.found.push(Item {
                    name: node.name.as_str(),
                    start: statement.start(),
                    body: &node.body,
                    interface: self.interface || abstract_,
                });
                let interface = std::mem::replace(&mut self.interface, false);
                self.visit_body(&node.body);
                self.interface = interface;
            }
            Stmt::ClassDef(node) => {
                self.found.push(Item {
                    name: node.name.as_str(),
                    start: statement.start(),
                    body: &node.body,
                    interface: false,
                });
                let interface = std::mem::replace(&mut self.interface, is_protocol(node));
                self.visit_body(&node.body);
                self.interface = interface;
            }
            Stmt::Assign(node) => {
                if let [Expr::Name(target)] = node.targets.as_slice() {
                    self.found.push(Item {
                        name: target.id.as_str(),
                        start: statement.start(),
                        body: &[],
                        interface: false,
                    });
                }
            }
            Stmt::AnnAssign(node) => {
                if let Expr::Name(target) = node.target.as_ref() {
                    self.found.push(Item {
                        name: target.id.as_str(),
                        start: statement.start(),
                        body: &[],
                        interface: false,
                    });
                }
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, _expression: &'a Expr) {}
}

fn is_protocol(node: &StmtClassDef) -> bool {
    node.bases().iter().any(|base| {
        let target = match base {
            Expr::Subscript(item) => item.value.as_ref(),
            other => other,
        };
        dotted_name(target).is_some_and(|name| name.rsplit('.').next() == Some("Protocol"))
    })
}
