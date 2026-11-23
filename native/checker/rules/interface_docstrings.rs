//! IDM005: `idioms/missing-interface-docstring`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::dotted_tail;
use crate::checker::analysis::nodes::{all_classes, has_docstring};
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, Stmt, StmtFunctionDef};
use serde_json::json;
use std::collections::HashSet;

const ABSTRACT_DECORATORS: [&str; 4] = [
    "abstractmethod",
    "abstractproperty",
    "abstractclassmethod",
    "abstractstaticmethod",
];
const EXEMPT_DECORATORS: [&str; 4] = ["override", "overload", "setter", "deleter"];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let mut found = Vec::new();
    for owner in all_classes(input.parsed.module()) {
        let protocol = owner.bases().iter().any(is_protocol);
        for statement in &owner.body {
            let Stmt::FunctionDef(method) = statement else {
                continue;
            };
            let decorators: HashSet<String> = method
                .decorator_list
                .iter()
                .map(|decorator| decorator_name(&decorator.expression))
                .collect();
            let (interface, label) = if ABSTRACT_DECORATORS
                .iter()
                .any(|name| decorators.contains(*name))
            {
                ("abstract", "Abstract")
            } else if protocol {
                ("protocol", "Protocol")
            } else {
                continue;
            };
            if !undocumented(method, &decorators) {
                continue;
            }
            let qualified = format!("{}.{}", owner.name, method.name);
            let (line, column) = input.place(input.parsed.function_start(method));
            found.push(input.violation(Finding {
                message: format!(
                    "{label} method {qualified} has a placeholder body and no docstring"
                ),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "class": owner.name.as_str(),
                    "method": method.name.as_str(),
                    "interface": interface,
                    "automatic_rewrite": false,
                }),
                hint: format!(
                    "Replace the body of {qualified} with a docstring that states its contract."
                ),
                fix: None,
            }));
        }
    }
    found
}

fn undocumented(method: &StmtFunctionDef, decorators: &HashSet<String>) -> bool {
    !EXEMPT_DECORATORS
        .iter()
        .any(|name| decorators.contains(*name))
        && !is_dunder(method.name.as_str())
        && !has_docstring(&method.body)
        && method.body.iter().all(is_placeholder)
}

fn is_protocol(base: &Expr) -> bool {
    let base = match base {
        Expr::Subscript(node) => node.value.as_ref(),
        other => other,
    };
    dotted_tail(base) == "Protocol"
}

fn decorator_name(decorator: &Expr) -> String {
    match decorator {
        Expr::Call(call) => dotted_tail(&call.func),
        other => dotted_tail(other),
    }
}

fn is_dunder(name: &str) -> bool {
    name != "__call__" && name.starts_with("__") && name.ends_with("__")
}

fn is_placeholder(statement: &Stmt) -> bool {
    match statement {
        Stmt::Pass(_) => true,
        Stmt::Expr(node) => node.value.is_ellipsis_literal_expr(),
        Stmt::Raise(node) => {
            let raised = match node.exc.as_deref() {
                Some(Expr::Call(call)) => Some(call.func.as_ref()),
                other => other,
            };
            matches!(raised, Some(Expr::Name(name)) if name.id.as_str() == "NotImplementedError")
        }
        _ => false,
    }
}
