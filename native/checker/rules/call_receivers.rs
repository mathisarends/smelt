//! CPX008: `complexity/inline-constructor-receiver`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{BindingCounts, Coverage, qualified_name};
use crate::checker::analysis::namespace::namespace_of;
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprCall, Stmt};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{HashMap, HashSet};

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let mut receivers = DirectReceivers { found: Vec::new() };
    receivers.visit_body(&module.body);
    if receivers.found.is_empty() {
        return Vec::new();
    }
    let counts = BindingCounts::new(module, Coverage::Full);
    if counts.get("*") > 0 {
        return Vec::new();
    }
    let classes: HashSet<&str> = module
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::ClassDef(node) if counts.get(node.name.as_str()) == 1 => Some(node.name.as_str()),
            _ => None,
        })
        .collect();
    let source = input.parsed.text.text.as_str();
    let imports: HashMap<String, String> = namespace_of(
        source,
        module,
        &input.source.module,
        input.source.is_package,
    )
    .imported
    .into_iter()
    .filter(|(local, _)| counts.get(local) == 1)
    .collect();
    let mut found = Vec::new();
    for (receiver, method) in receivers.found {
        if !constructs(&receiver.func, &classes, &imports) {
            continue;
        }
        let name = unparse(source, &receiver.func);
        let (line, column) = input.place(receiver.start());
        let (end_line, end_column) = input.place(receiver.end());
        found.push(input.violation(Finding {
            message: format!("Name the {name} instance before calling .{method}()"),
            line,
            column,
            end_line: Some(end_line),
            end_column: Some(end_column),
            expected: json!({"constructor": name, "method": method}),
            hint: "Assign the constructor call to a descriptive variable and call the method on that variable.".to_owned(),
            fix: None,
        }));
    }
    found
}

fn constructs(callee: &Expr, classes: &HashSet<&str>, imports: &HashMap<String, String>) -> bool {
    if let Expr::Name(name) = callee
        && classes.contains(name.id.as_str())
    {
        return true;
    }
    qualified_name(callee, imports).is_some_and(|qualified| {
        let last = qualified.rsplit('.').next().unwrap_or(&qualified);
        last.trim_start_matches('_')
            .chars()
            .next()
            .is_some_and(char::is_uppercase)
    })
}

/// Method calls on a fresh call, as the whole value of a statement.
struct DirectReceivers<'a> {
    found: Vec<(&'a ExprCall, &'a str)>,
}

impl<'a> Visitor<'a> for DirectReceivers<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let value = match statement {
            Stmt::Assign(node) => Some(node.value.as_ref()),
            Stmt::AnnAssign(node) => node.value.as_deref(),
            Stmt::Return(node) => node.value.as_deref(),
            Stmt::Expr(node) => Some(node.value.as_ref()),
            _ => None,
        };
        if let Some(value) = value {
            let call = match value {
                Expr::Await(awaited) => awaited.value.as_ref(),
                other => other,
            };
            if let Expr::Call(call) = call
                && let Expr::Attribute(attribute) = call.func.as_ref()
                && let Expr::Call(receiver) = attribute.value.as_ref()
            {
                self.found.push((receiver, attribute.attr.as_str()));
            }
        }
        visitor::walk_stmt(self, statement);
    }
}
