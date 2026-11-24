//! Render approved statement moves while preserving source text and trivia.
use super::{Owner, Section};
use crate::checker::analysis::dump::{dump_body, dump_body_with};
use crate::checker::analysis::edits::{is_python_space, python_lines};
use crate::checker::source::ParsedSource;
use regex::Regex;
use ruff_python_ast::token::TokenKind;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_python_parser::parse_module;
use ruff_text_size::{Ranged, TextRange, TextSize};
use std::sync::LazyLock;

static CODING: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"coding[:=]\s*([-\w.]+)").expect("the coding pattern is valid"));
static FILE_PRAGMA: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*#\s*(?:ruff:|mypy:|pyright:|flake8:|type:\s*ignore|smelt:\s*ignore-file)")
        .expect("the pragma pattern is valid")
});
static FIX_BOUNDARY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^\s*#\s*(?:fmt:\s*(?:off|skip)\b|pylint:\s*(?:disable|enable)\b)")
        .expect("the boundary pattern is valid")
});
const ENCODING_LINES: usize = 2;

/// The source with the blocks of `section` in their expected order.
pub fn rewrite_order(parsed: &ParsedSource, section: &Section<'_>) -> Result<String, String> {
    let source = parsed.text.text.as_str();
    if FIX_BOUNDARY.is_match(source) {
        return Err("formatter or suppression region prevents safe ordering fixes".to_owned());
    }
    if python_lines(source, false).into_iter().any(|line| {
        let indentation = line.len() - line.trim_start_matches(is_python_space).len();
        line[..indentation].contains('\t')
    }) {
        return Err("tab indentation requires manual ordering review".to_owned());
    }
    compile_check(&parsed.module().body)?;
    let parts = parts(parsed, section)?;
    let (start, end) = (parts[0].0, parts[parts.len() - 1].1);
    let blocks: Vec<&str> = section
        .order
        .iter()
        .map(|index| &source[parts[*index].0..parts[*index].1])
        .collect();
    let mut rewritten = String::with_capacity(source.len() + 1);
    rewritten.push_str(&source[..start]);
    for (index, block) in blocks.iter().enumerate() {
        rewritten.push_str(block);
        if blocks.get(index + 1).is_some_and(|next| {
            !block.is_empty() && !block.ends_with(['\n', '\r']) && !next.starts_with(['\n', '\r'])
        }) {
            rewritten.push('\n');
        }
    }
    rewritten.push_str(&source[end..]);
    if !source.ends_with(['\n', '\r']) && rewritten.ends_with('\n') {
        rewritten.pop();
    }
    let reparsed = parse_module(&rewritten)
        .map_err(|_| "ordering rewrite did not preserve valid source".to_owned())?;
    let owner = section.owner.body();
    let mut replacement: Vec<&Stmt> = owner[..section.blocks[0].first].iter().collect();
    for index in &section.order {
        replacement.extend(section.blocks[*index].nodes);
    }
    replacement.extend(&owner[section.blocks[section.blocks.len() - 1].stop..]);
    let expected = dump_body_with(source, &parsed.module().body, Some((owner, replacement)));
    if dump_body(&rewritten, &reparsed.syntax().body) != expected {
        return Err("ordering rewrite changed more than the approved statement order".to_owned());
    }
    Ok(rewritten)
}

/// The source span of each block: its statements, the comments and blank lines
/// before them, and comments indented into a definition after it.
fn parts(parsed: &ParsedSource, section: &Section<'_>) -> Result<Vec<(usize, usize)>, String> {
    let source = parsed.text.text.as_str();
    let lines = python_lines(source, true);
    let mut offsets = vec![0];
    for line in &lines {
        offsets.push(offsets[offsets.len() - 1] + line.len());
    }
    let mut cursor = header_lines(parsed, section.owner)?;
    let mut spans = Vec::new();
    for statement in section.owner.body() {
        let end = statement_end(parsed, statement, &lines);
        spans.push((offsets[cursor], offsets[end]));
        cursor = end;
    }
    let parts: Vec<(usize, usize)> = section
        .blocks
        .iter()
        .map(|block| (spans[block.first].0, spans[block.stop - 1].1))
        .collect();
    if parts.iter().any(|(start, end)| start >= end) {
        return Err("overlapping source lines cannot be fixed safely".to_owned());
    }
    Ok(parts)
}

/// The lines before the first movable statement: a module's shebang, coding and
/// file pragmas, or a class header up to its suite.
fn header_lines(parsed: &ParsedSource, owner: Owner<'_>) -> Result<usize, String> {
    let class = match owner {
        Owner::Module(body) => {
            let first = body.first().map_or(1, |statement| {
                parsed.text.line_at(usize::from(statement.start()))
            });
            let mut fixed = 0;
            for (index, line) in python_lines(&parsed.text.text, false)
                .into_iter()
                .take(first - 1)
                .enumerate()
            {
                if (index == 0 && line.starts_with("#!"))
                    || (index < ENCODING_LINES && CODING.is_match(line))
                    || FILE_PRAGMA.is_match(line)
                {
                    fixed = index + 1;
                }
            }
            return Ok(fixed);
        }
        Owner::Class(class) => class,
    };
    let mut depth = 0usize;
    for token in parsed.parsed.tokens().after(parsed.class_start(class)) {
        match token.kind() {
            TokenKind::Lpar | TokenKind::Lsqb | TokenKind::Lbrace => depth += 1,
            TokenKind::Rpar | TokenKind::Rsqb | TokenKind::Rbrace => {
                depth = depth.saturating_sub(1)
            }
            TokenKind::Colon if depth == 0 => {
                return Ok(parsed.text.line_at(usize::from(token.end())));
            }
            _ => {}
        }
    }
    Err("cannot identify the class suite for a safe ordering fix".to_owned())
}

/// The last line of a statement; a definition also owns the comments indented
/// into its body that follow it.
fn statement_end(parsed: &ParsedSource, statement: &Stmt, lines: &[&str]) -> usize {
    let mut end = parsed.text.line_at(usize::from(statement.end()));
    if !matches!(statement, Stmt::FunctionDef(_) | Stmt::ClassDef(_)) {
        return end;
    }
    let column = parsed
        .text
        .position(usize::from(parsed.statement_start(statement)))
        .1;
    for (index, line) in lines.iter().enumerate().skip(end) {
        let stripped = line.trim_start_matches(is_python_space);
        if stripped.trim_end_matches(is_python_space).is_empty() {
            continue;
        }
        let indentation = line.len() - line.trim_start_matches([' ', '\t']).len();
        if stripped.starts_with('#') && indentation > column {
            end = index + 1;
        } else {
            break;
        }
    }
    end
}

/// The compile-time errors `compile` raises for a parsed module that matter here:
/// misplaced future imports, a module-level `nonlocal`, and `return`, `yield`,
/// `await`, `break` or `continue` outside their scope.
fn compile_check(body: &[Stmt]) -> Result<(), String> {
    let fail = |message: &str| Err(format!("source cannot be safely compiled: {message}"));
    let mut boundary = false;
    for (index, statement) in body.iter().enumerate() {
        let future = matches!(statement, Stmt::ImportFrom(import)
            if import.level == 0
                && import.module.as_ref().is_some_and(|module| module.as_str() == "__future__"));
        let docstring = index == 0
            && matches!(statement, Stmt::Expr(node) if node.value.is_string_literal_expr());
        if future && boundary {
            return fail("from __future__ imports must occur at the beginning of the file");
        }
        boundary |= !future && !docstring;
    }
    if body
        .iter()
        .any(|statement| matches!(statement, Stmt::Nonlocal(_)))
    {
        return fail("nonlocal declaration not allowed at module level");
    }
    let mut scopes = Scopes::default();
    scopes.visit_body(body);
    match scopes.errors.into_iter().min_by_key(|(offset, _)| *offset) {
        Some((_, message)) => fail(message),
        None => Ok(()),
    }
}

/// Where statements and expressions run: in a function, async or not, and in a loop.
#[derive(Default)]
struct Scopes {
    function: bool,
    asynchronous: bool,
    in_loop: bool,
    errors: Vec<(TextSize, &'static str)>,
}

impl Scopes {
    fn error(&mut self, range: TextRange, message: &'static str) {
        self.errors.push((range.start(), message));
    }

    fn nested(&mut self, function: bool, asynchronous: bool, body: &[Stmt]) {
        let saved = (self.function, self.asynchronous, self.in_loop);
        (self.function, self.asynchronous, self.in_loop) = (function, asynchronous, false);
        self.visit_body(body);
        (self.function, self.asynchronous, self.in_loop) = saved;
    }
}

impl<'a> Visitor<'a> for Scopes {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(function) => {
                for decorator in &function.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                self.nested(true, function.is_async, &function.body);
            }
            Stmt::ClassDef(class) => {
                for decorator in &class.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                for base in class.bases() {
                    self.visit_expr(base);
                }
                self.nested(false, false, &class.body);
            }
            Stmt::Return(_) if !self.function => {
                self.error(statement.range(), "'return' outside function")
            }
            Stmt::Break(_) if !self.in_loop => {
                self.error(statement.range(), "'break' outside loop")
            }
            Stmt::Continue(_) if !self.in_loop => {
                self.error(statement.range(), "'continue' not properly in loop");
            }
            Stmt::For(_) | Stmt::While(_) => {
                let (body, orelse): (&[Stmt], &[Stmt]) = match statement {
                    Stmt::For(item) => {
                        self.visit_expr(&item.iter);
                        (&item.body, &item.orelse)
                    }
                    Stmt::While(item) => {
                        self.visit_expr(&item.test);
                        (&item.body, &item.orelse)
                    }
                    _ => unreachable!("matched a loop"),
                };
                let saved = std::mem::replace(&mut self.in_loop, true);
                self.visit_body(body);
                self.in_loop = saved;
                self.visit_body(orelse);
            }
            _ => visitor::walk_stmt(self, statement),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Lambda(_) => {}
            Expr::Yield(_) | Expr::YieldFrom(_) if !self.function => {
                self.error(expression.range(), "'yield' outside function");
            }
            Expr::Await(_) if !self.function => {
                self.error(expression.range(), "'await' outside function");
            }
            Expr::Await(_) if !self.asynchronous => {
                self.error(expression.range(), "'await' outside async function");
            }
            _ => visitor::walk_expr(self, expression),
        }
    }
}
