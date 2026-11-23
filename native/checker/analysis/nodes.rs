//! Syntax helpers shared by the ported rules.
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    ElifElseClause, ExceptHandler, Expr, MatchCase, ModExpression, ModModule, Stmt, StmtClassDef,
};
use ruff_python_parser::{Parsed, parse_expression};
use ruff_text_size::Ranged;
use std::collections::VecDeque;

/// `ast.parse(text, mode="eval")`: Ruff alone accepts more than CPython does here.
pub fn parse_eval(text: &str) -> Option<Parsed<ModExpression>> {
    let first_line = text
        .split('\n')
        .find(|line| !line.trim().is_empty() && !line.trim_start().starts_with('#'))?;
    if first_line.starts_with([' ', '\t', '\x0c']) {
        return None;
    }
    let parsed = parse_expression(text).ok()?;
    let body = parsed.expr();
    let starred = match body {
        Expr::Starred(_) => true,
        Expr::Tuple(tuple) => tuple.elts.iter().any(Expr::is_starred_expr),
        _ => false,
    };
    let bare = matches!(body, Expr::Named(_) | Expr::Yield(_) | Expr::YieldFrom(_))
        && !text[..usize::from(body.start())].contains('(');
    (!starred && !bare).then_some(parsed)
}

/// The items of a subscript: tuple elements, or the slice itself.
pub fn subscript_parts(slice: &Expr) -> &[Expr] {
    match slice {
        Expr::Tuple(tuple) => &tuple.elts,
        other => std::slice::from_ref(other),
    }
}

/// Every class in the module, however deeply nested.
pub fn all_classes(module: &ModModule) -> Vec<&StmtClassDef> {
    let mut collector = ClassCollector { found: Vec::new() };
    collector.visit_body(&module.body);
    collector.found
}

/// Whether `expression` is what CPython parses to `ast.Constant`.
pub fn is_constant(expression: &Expr) -> bool {
    matches!(
        expression,
        Expr::StringLiteral(_)
            | Expr::BytesLiteral(_)
            | Expr::NumberLiteral(_)
            | Expr::BooleanLiteral(_)
            | Expr::NoneLiteral(_)
            | Expr::EllipsisLiteral(_)
    )
}

/// `str.isupper`: at least one uppercase character and no lowercase or titlecase one.
pub fn is_upper(text: &str) -> bool {
    let mut cased = false;
    for character in text.chars() {
        if character.is_lowercase() || is_titlecase(character) {
            return false;
        }
        cased |= character.is_uppercase();
    }
    cased
}

fn is_titlecase(character: char) -> bool {
    matches!(
        character,
        '\u{01C5}'
            | '\u{01C8}'
            | '\u{01CB}'
            | '\u{01F2}'
            | '\u{1F88}'..='\u{1F8F}'
            | '\u{1F98}'..='\u{1F9F}'
            | '\u{1FA8}'..='\u{1FAF}'
            | '\u{1FBC}'
            | '\u{1FCC}'
            | '\u{1FFC}'
    )
}

/// One node of `ast.walk` that can hold statements.
enum Block<'a> {
    Statement(&'a Stmt),
    /// The `If` CPython builds for an `elif` and the clauses after it.
    Elif(&'a [ElifElseClause]),
    Handler(&'a ExceptHandler),
    Case(&'a MatchCase),
}

/// Every statement in `ast.walk` order, which is breadth-first.
pub fn walk_statements(module: &ModModule) -> Vec<&Stmt> {
    let mut found = Vec::new();
    let mut queue: VecDeque<Block<'_>> = module.body.iter().map(Block::Statement).collect();
    while let Some(block) = queue.pop_front() {
        match block {
            Block::Statement(statement) => {
                found.push(statement);
                enqueue_children(statement, &mut queue);
            }
            Block::Elif(clauses) => {
                if let Some((clause, rest)) = clauses.split_first() {
                    queue.extend(clause.body.iter().map(Block::Statement));
                    enqueue_clauses(rest, &mut queue);
                }
            }
            Block::Handler(handler) => {
                let ExceptHandler::ExceptHandler(handler) = handler;
                queue.extend(handler.body.iter().map(Block::Statement));
            }
            Block::Case(case) => queue.extend(case.body.iter().map(Block::Statement)),
        }
    }
    found
}

fn enqueue_clauses<'a>(clauses: &'a [ElifElseClause], queue: &mut VecDeque<Block<'a>>) {
    if let Some(clause) = clauses.first() {
        if clause.test.is_some() {
            queue.push_back(Block::Elif(clauses));
        } else {
            queue.extend(clause.body.iter().map(Block::Statement));
        }
    }
}

fn enqueue_children<'a>(statement: &'a Stmt, queue: &mut VecDeque<Block<'a>>) {
    match statement {
        Stmt::FunctionDef(node) => queue.extend(node.body.iter().map(Block::Statement)),
        Stmt::ClassDef(node) => queue.extend(node.body.iter().map(Block::Statement)),
        Stmt::For(node) => {
            queue.extend(node.body.iter().map(Block::Statement));
            queue.extend(node.orelse.iter().map(Block::Statement));
        }
        Stmt::While(node) => {
            queue.extend(node.body.iter().map(Block::Statement));
            queue.extend(node.orelse.iter().map(Block::Statement));
        }
        Stmt::If(node) => {
            queue.extend(node.body.iter().map(Block::Statement));
            enqueue_clauses(&node.elif_else_clauses, queue);
        }
        Stmt::With(node) => queue.extend(node.body.iter().map(Block::Statement)),
        Stmt::Match(node) => queue.extend(node.cases.iter().map(Block::Case)),
        Stmt::Try(node) => {
            queue.extend(node.body.iter().map(Block::Statement));
            queue.extend(node.handlers.iter().map(Block::Handler));
            queue.extend(node.orelse.iter().map(Block::Statement));
            queue.extend(node.finalbody.iter().map(Block::Statement));
        }
        _ => {}
    }
}

struct ClassCollector<'a> {
    found: Vec<&'a StmtClassDef>,
}

impl<'a> Visitor<'a> for ClassCollector<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if let Stmt::ClassDef(node) = statement {
            self.found.push(node);
        }
        visitor::walk_stmt(self, statement);
    }
}
