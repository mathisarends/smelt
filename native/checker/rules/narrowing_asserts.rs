//! IDM007: `idioms/duplicate-narrowing-assert`.
use super::{Finding, RuleInput};
use crate::checker::analysis::nodes::all_classes;
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::{
    BoolOp, CmpOp, ExceptHandler, Expr, Stmt, StmtAssert, StmtClassDef, StmtFunctionDef,
};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::HashMap;

const MIN_METHODS: usize = 2;
const HINT: &str = "Make the attribute required during construction, or read it through one property that raises an error naming the missing setup.";

struct Site<'a> {
    method: &'a str,
    node: &'a StmtAssert,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let mut found = Vec::new();
    for owner in all_classes(input.parsed.module()) {
        check_class(input, owner, &mut found);
    }
    found
}

fn check_class(input: &RuleInput<'_>, owner: &StmtClassDef, found: &mut Vec<Violation>) {
    let source = input.parsed.text.text.as_str();
    let mut positions: HashMap<String, usize> = HashMap::new();
    let mut sites: Vec<(String, Vec<Site<'_>>)> = Vec::new();
    for statement in &owner.body {
        let Stmt::FunctionDef(method) = statement else {
            continue;
        };
        let Some(receiver) = receiver(method) else {
            continue;
        };
        let mut asserted = Vec::new();
        collect_asserts(&method.body, &mut asserted);
        for node in asserted {
            let mut tests = Vec::new();
            collect_narrowings(&node.test, receiver, &mut tests);
            for test in tests {
                let expression = unparse(source, test);
                let index = *positions.entry(expression.clone()).or_insert_with(|| {
                    sites.push((expression, Vec::new()));
                    sites.len() - 1
                });
                sites[index].1.push(Site {
                    method: method.name.as_str(),
                    node,
                });
            }
        }
    }
    for (expression, group) in &sites {
        let mut methods: Vec<&str> = Vec::new();
        for site in group {
            if !methods.contains(&site.method) {
                methods.push(site.method);
            }
        }
        if methods.len() < MIN_METHODS {
            continue;
        }
        let (line, column) = input.place(group[0].node.start());
        let lines: Vec<usize> = group
            .iter()
            .map(|site| input.place(site.node.start()).0)
            .collect();
        found.push(input.violation(Finding {
            message: format!(
                "assert {expression} repeats in {} methods of {}: {}",
                methods.len(),
                owner.name,
                methods.join(", "),
            ),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "class": owner.name.as_str(),
                "expression": expression,
                "methods": methods,
                "lines": lines,
                "automatic_rewrite": false,
            }),
            hint: HINT.to_owned(),
            fix: None,
        }));
    }
}

fn receiver(method: &StmtFunctionDef) -> Option<&str> {
    let is_static = method.decorator_list.iter().any(|decorator| {
        matches!(&decorator.expression, Expr::Name(name) if name.id.as_str() == "staticmethod")
    });
    if is_static {
        return None;
    }
    let parameters = &method.parameters;
    parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .next()
        .map(|parameter| parameter.parameter.name.as_str())
}

/// Asserts of this scope in the order of the Python stack walk: last statement first.
fn collect_asserts<'a>(body: &'a [Stmt], found: &mut Vec<&'a StmtAssert>) {
    for statement in body.iter().rev() {
        match statement {
            Stmt::Assert(node) => found.push(node),
            Stmt::For(node) => {
                collect_asserts(&node.orelse, found);
                collect_asserts(&node.body, found);
            }
            Stmt::While(node) => {
                collect_asserts(&node.orelse, found);
                collect_asserts(&node.body, found);
            }
            Stmt::If(node) => {
                for clause in node.elif_else_clauses.iter().rev() {
                    collect_asserts(&clause.body, found);
                }
                collect_asserts(&node.body, found);
            }
            Stmt::With(node) => collect_asserts(&node.body, found),
            Stmt::Match(node) => {
                for case in node.cases.iter().rev() {
                    collect_asserts(&case.body, found);
                }
            }
            Stmt::Try(node) => {
                collect_asserts(&node.finalbody, found);
                collect_asserts(&node.orelse, found);
                for handler in node.handlers.iter().rev() {
                    let ExceptHandler::ExceptHandler(handler) = handler;
                    collect_asserts(&handler.body, found);
                }
                collect_asserts(&node.body, found);
            }
            _ => {}
        }
    }
}

fn collect_narrowings<'a>(test: &'a Expr, receiver: &str, found: &mut Vec<&'a Expr>) {
    if let Expr::BoolOp(node) = test
        && node.op == BoolOp::And
    {
        for value in &node.values {
            collect_narrowings(value, receiver, found);
        }
    } else if is_not_none(test, receiver) || is_instance(test, receiver) {
        found.push(test);
    }
}

fn is_not_none(test: &Expr, receiver: &str) -> bool {
    let Expr::Compare(node) = test else {
        return false;
    };
    matches!(node.ops.as_ref(), [CmpOp::IsNot])
        && matches!(node.comparators().first(), Some(Expr::NoneLiteral(_)))
        && on_receiver(node.first_operand(), receiver)
}

fn is_instance(test: &Expr, receiver: &str) -> bool {
    let Expr::Call(node) = test else {
        return false;
    };
    matches!(node.func.as_ref(), Expr::Name(name) if name.id.as_str() == "isinstance")
        && node
            .arguments
            .args
            .first()
            .is_some_and(|first| on_receiver(first, receiver))
}

fn on_receiver(expression: &Expr, receiver: &str) -> bool {
    let Expr::Attribute(attribute) = expression else {
        return false;
    };
    let mut node = attribute.value.as_ref();
    while let Expr::Attribute(inner) = node {
        node = inner.value.as_ref();
    }
    matches!(node, Expr::Name(name) if name.id.as_str() == receiver)
}
