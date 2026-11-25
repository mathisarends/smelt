//! IDM006: `idioms/missing-broad-except-reason`.
use super::{Finding, RuleInput};
use crate::checker::analysis::comments::{Comments, is_reason};
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{ExceptHandler, ExceptHandlerExceptHandler, Expr, Stmt};
use ruff_text_size::{Ranged, TextSize};
use serde_json::json;

const BROAD: [&str; 2] = ["Exception", "BaseException"];
const BARE: &str = "bare";
const HINT: &str = "Catch the specific exceptions you can handle, or add a comment explaining why this handler must be broad.";

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let mut collector = Handlers { found: Vec::new() };
    collector.visit_body(&input.parsed.module().body);
    let mut handlers: Vec<(&ExceptHandlerExceptHandler, &str)> = collector
        .found
        .into_iter()
        .filter_map(|handler| {
            let caught = broad_type(handler)?;
            (!always_raises(handler)).then_some((handler, caught))
        })
        .collect();
    if handlers.is_empty() {
        return Vec::new();
    }
    handlers.sort_by_key(|(handler, _)| handler.start());
    let comments = Comments::scan(input.parsed);
    let mut found = Vec::new();
    for (handler, caught) in handlers {
        let (line, column) = input.place(handler.start());
        let (body_line, _) = input.place(statement_start(input.parsed, &handler.body[0]));
        if explains(&comments, line, body_line) {
            continue;
        }
        let subject = if caught == BARE {
            "Bare except".to_owned()
        } else {
            format!("Handler for {caught}")
        };
        found.push(input.violation(Finding {
            message: format!("{subject} does not say why it catches every failure"),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({"caught": caught, "automatic_rewrite": false}),
            hint: HINT.to_owned(),
            fix: None,
        }));
    }
    found
}

fn statement_start(parsed: &ParsedSource, statement: &Stmt) -> TextSize {
    match statement {
        Stmt::FunctionDef(node) => parsed.function_start(node),
        Stmt::ClassDef(node) => parsed.class_start(node),
        other => other.start(),
    }
}

/// The except header, lines up to the first body statement, and comments right above.
fn explains(comments: &Comments<'_>, handler_line: usize, body_line: usize) -> bool {
    (handler_line..=body_line)
        .chain(comments.block_above(handler_line))
        .filter_map(|line| comments.text(line))
        .any(is_reason)
}

fn broad_type(handler: &ExceptHandlerExceptHandler) -> Option<&str> {
    let Some(caught) = handler.type_.as_deref() else {
        return Some(BARE);
    };
    let candidates = match caught {
        Expr::Tuple(node) => node.elts.as_slice(),
        other => std::slice::from_ref(other),
    };
    candidates.iter().find_map(|candidate| {
        let name = match candidate {
            Expr::Name(node) => node.id.as_str(),
            Expr::Attribute(node)
                if matches!(node.value.as_ref(), Expr::Name(owner) if owner.id.as_str() == "builtins") =>
            {
                node.attr.as_str()
            }
            _ => return None,
        };
        BROAD.contains(&name).then_some(name)
    })
}

fn always_raises(handler: &ExceptHandlerExceptHandler) -> bool {
    if !matches!(handler.body.last(), Some(Stmt::Raise(_))) {
        return false;
    }
    let mut exits = LocalExits { found: false };
    exits.visit_body(&handler.body);
    !exits.found
}

/// A return, break or continue outside nested functions and classes.
struct LocalExits {
    found: bool,
}

impl<'a> Visitor<'a> for LocalExits {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => {}
            Stmt::Return(_) | Stmt::Break(_) | Stmt::Continue(_) => self.found = true,
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, _expression: &'a Expr) {}
}

struct Handlers<'a> {
    found: Vec<&'a ExceptHandlerExceptHandler>,
}

impl<'a> Visitor<'a> for Handlers<'a> {
    fn visit_except_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        self.found.push(node);
        visitor::walk_except_handler(self, handler);
    }

    fn visit_expr(&mut self, _expression: &'a Expr) {}
}
