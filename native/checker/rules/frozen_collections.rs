//! MOD011: `modeling/mutable-field-in-frozen-dataclass`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{annotation_name, qualified_name, unshadowed_bindings};
use crate::checker::analysis::nodes::all_classes;
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, Stmt, StmtClassDef};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::HashMap;

fn alternative(kind: &str) -> Option<&'static str> {
    match kind {
        "builtins.dict" => Some("a defensive copy behind MappingProxyType, exposed as Mapping"),
        "builtins.list" => Some("tuple"),
        "builtins.set" => Some("frozenset"),
        _ => None,
    }
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let classes = all_classes(module);
    if classes.iter().all(|owner| owner.decorator_list.is_empty()) {
        return Vec::new();
    }
    let bindings = unshadowed_bindings(&input.parsed.text.text, module, &["dict", "list", "set"]);
    let mut found = Vec::new();
    for owner in classes {
        if !is_frozen(owner, &bindings) {
            continue;
        }
        for field in &owner.body {
            let Stmt::AnnAssign(field) = field else {
                continue;
            };
            let Expr::Name(target) = field.target.as_ref() else {
                continue;
            };
            let Some(kind) = annotation_name(&field.annotation, &bindings) else {
                continue;
            };
            let Some(replacement) = alternative(&kind) else {
                continue;
            };
            let (line, column) = input.place(field.start());
            found.push(input.violation(Finding {
                message: format!(
                    "Frozen dataclass {}.{} contains mutable {} contents",
                    owner.name,
                    target.id,
                    kind.strip_prefix("builtins.").unwrap_or(&kind)
                ),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "class": owner.name.as_str(),
                    "field": target.id.as_str(),
                    "mutable_type": kind,
                    "automatic_rewrite": false,
                }),
                hint: format!("Review ownership; consider {replacement}."),
                fix: None,
            }));
        }
    }
    found
}

fn is_frozen(owner: &StmtClassDef, bindings: &HashMap<String, String>) -> bool {
    owner.decorator_list.iter().any(|decorator| {
        let Expr::Call(call) = &decorator.expression else {
            return false;
        };
        let keywords = &call.arguments.keywords;
        qualified_name(&call.func, bindings).as_deref() == Some("dataclasses.dataclass")
            && keywords.iter().all(|keyword| keyword.arg.is_some())
            && keywords.iter().any(|keyword| {
                keyword.arg.as_ref().is_some_and(|name| name == "frozen")
                    && matches!(&keyword.value, Expr::BooleanLiteral(value) if value.value)
            })
    })
}
