//! IDM003: `idioms/prefer-callable-instance`.
use super::{Finding, RuleInput};
use crate::checker::analysis::nodes::all_classes;
use crate::checker::analysis::unparse::unparse_without_receiver;
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, Stmt, StmtClassDef, StmtFunctionDef};
use serde_json::json;
use std::collections::HashSet;

const HINT: &str = "Consider __call__ and a class name describing the operation. Preserve constructor and call-time arguments separately; review all callers and public compatibility before renaming.";

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let source = input.parsed.text.text.as_str();
    let mut found = Vec::new();
    for class in all_classes(input.parsed.module()) {
        let methods = plain_methods(class);
        let mut public = methods
            .iter()
            .filter(|method| !method.name.as_str().starts_with('_'));
        let (Some(method), None) = (public.next(), public.next()) else {
            continue;
        };
        if methods.iter().any(|item| item.name.as_str() == "__call__") {
            continue;
        }
        let constructor = methods
            .iter()
            .find(|item| item.name.as_str() == "__init__")
            .map_or_else(String::new, |item| {
                unparse_without_receiver(source, &item.parameters)
            });
        let (line, column) = input.place(input.parsed.class_start(class));
        found.push(input.violation(Finding {
            message: format!(
                "review {}.{} as __call__ with an operation-based class name",
                class.name, method.name
            ),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "class": class.name.as_str(),
                "method": method.name.as_str(),
                "candidate_method": "__call__",
                "constructor_signature": constructor,
                "operation_signature": unparse_without_receiver(source, &method.parameters),
                "async_operation": method.is_async,
                "review_class_name": true,
                "review_required": true,
                "automatic_rewrite": false,
            }),
            hint: HINT.to_owned(),
            fix: None,
        }));
    }
    found
}

fn plain_methods(class: &StmtClassDef) -> Vec<&StmtFunctionDef> {
    let inherits = class
        .arguments
        .as_ref()
        .is_some_and(|arguments| !arguments.args.is_empty() || !arguments.keywords.is_empty());
    if inherits || !class.decorator_list.is_empty() {
        return Vec::new();
    }
    let mut methods = Vec::new();
    let mut names = HashSet::new();
    for node in &class.body {
        match node {
            Stmt::Pass(_) => continue,
            Stmt::Expr(item) if matches!(item.value.as_ref(), Expr::StringLiteral(_)) => continue,
            Stmt::FunctionDef(method) => {
                let name = method.name.as_str();
                let special = name.starts_with("__")
                    && name.ends_with("__")
                    && name != "__init__"
                    && name != "__call__";
                let has_receiver =
                    !method.parameters.posonlyargs.is_empty() || !method.parameters.args.is_empty();
                if !method.decorator_list.is_empty()
                    || !has_receiver
                    || special
                    || !names.insert(name)
                {
                    return Vec::new();
                }
                methods.push(method);
            }
            _ => return Vec::new(),
        }
    }
    methods
}
