//! MOD003: `modeling/prefer-str-enum-discriminator`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{Names, typing_bindings, typing_name};
use crate::checker::analysis::nodes::{all_classes, parse_eval, subscript_parts};
use crate::checker::analysis::repr::str_repr;
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::Ranged;
use serde_json::json;

const DISCRIMINATORS: [&str; 3] = ["type", "kind", "tag"];
const WRAPPERS: [&str; 3] = ["Annotated", "Required", "NotRequired"];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let target = input.targets.for_path(&input.source.absolute);
    if target.below(3, 11) {
        return Vec::new();
    }
    let module = input.parsed.module();
    let bindings = typing_bindings(module);
    let mut found = Vec::new();
    for owner in all_classes(module) {
        for statement in &owner.body {
            let Stmt::AnnAssign(node) = statement else {
                continue;
            };
            let Expr::Name(name) = node.target.as_ref() else {
                continue;
            };
            let field = name.id.as_str();
            if !DISCRIMINATORS.contains(&field) {
                continue;
            }
            let Some(value) = literal_string(&node.annotation, &bindings) else {
                continue;
            };
            let (line, column) = input.place(statement.start());
            let (end_line, end_column) = input.place(statement.end());
            let class = owner.name.as_str();
            found.push(input.violation(Finding {
                message: format!(
                    "consider a StrEnum member for {class}.{field}'s discriminator {}",
                    str_repr(&value)
                ),
                line,
                column,
                end_line: Some(end_line),
                end_column: Some(end_column),
                expected: json!({
                    "class": class,
                    "field": field,
                    "value": value,
                    "python": target.to_json(),
                    "automatic_rewrite": false,
                }),
                hint: "Use Literal[EnumType.MEMBER] with a shared StrEnum; use the same member in the default and preserve the explicit wire string.".to_owned(),
                fix: None,
            }));
        }
    }
    found
}

fn literal_string(annotation: &Expr, bindings: &Names<'_>) -> Option<String> {
    if let Expr::StringLiteral(text) = annotation {
        let parsed = parse_eval(text.value.to_str())?;
        return unwrap_literal(parsed.expr(), bindings);
    }
    unwrap_literal(annotation, bindings)
}

fn unwrap_literal(annotation: &Expr, bindings: &Names<'_>) -> Option<String> {
    let mut node = annotation;
    while let Expr::Subscript(item) = node {
        let name = typing_name(&item.value, bindings);
        let parts = subscript_parts(&item.slice);
        if let (Some(wrapper), Some(first)) = (name, parts.first())
            && WRAPPERS.contains(&wrapper)
        {
            node = first;
            continue;
        }
        if name == Some("Literal")
            && let [Expr::StringLiteral(value)] = parts
        {
            return Some(value.value.to_str().to_owned());
        }
        break;
    }
    None
}
