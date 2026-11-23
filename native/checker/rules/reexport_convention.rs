//! VIS008: `visibility/reexport-convention`.
use super::exports::explicit_exports;
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{BindingCounts, Coverage};
use crate::checker::analysis::edits::{fix, import_insertion};
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use ruff_python_ast::{Alias, Expr, ModModule, Stmt};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::HashSet;

const DEFAULT_STYLE: &str = "all";

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    if !input.source.is_package {
        return Vec::new();
    }
    let policy = input
        .contracts
        .policy_for(&input.source.module, "reexports");
    let style = policy
        .as_ref()
        .and_then(|origin| origin.value.as_str())
        .unwrap_or(DEFAULT_STYLE);
    if style == "allow" {
        return Vec::new();
    }
    let using_all = style == "all";
    let module = input.parsed.module();
    let mismatched = mismatched_reexports(module, style);
    mismatched
        .iter()
        .map(|alias| {
            let name = alias.name.as_str();
            let repair = if using_all {
                all_fix(input.parsed, alias, &mismatched)
            } else {
                Some(alias_fix(input.parsed, alias))
            };
            let (line, column) = input.place(alias.start());
            input.violation(Finding {
                message: if using_all {
                    format!("use a plain import for {name} under the __all__ convention")
                } else {
                    format!("mark the re-export of {name} with a same-name alias")
                },
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({"style": style, "name": name}),
                hint: if using_all {
                    format!(
                        "Use a plain import and list {name} in __all__ if it belongs to the package API."
                    )
                } else {
                    format!(
                        "Use {name} as {name}. Keep __all__ where callers rely on that list."
                    )
                },
                fix: repair,
            })
        })
        .collect()
}

fn mismatched_reexports<'a>(module: &'a ModModule, style: &str) -> Vec<&'a Alias> {
    let exports = if style == "alias" {
        match explicit_exports(module) {
            Some(exports) => exports,
            None => return Vec::new(),
        }
    } else {
        HashSet::new()
    };
    let counts = BindingCounts::new(module, Coverage::Full);
    if counts.get("*") != 0 {
        return Vec::new();
    }
    let mut found = Vec::new();
    for statement in &module.body {
        let names = match statement {
            Stmt::Import(node) => &node.names,
            Stmt::ImportFrom(node)
                if !(node.level == 0
                    && node
                        .module
                        .as_ref()
                        .is_some_and(|name| name.as_str() == "__future__")) =>
            {
                &node.names
            }
            _ => continue,
        };
        for alias in names {
            let name = alias.name.as_str();
            let local = alias.asname.as_ref().map(|item| item.as_str());
            if name.contains('.')
                || matches!(name, "__all__" | "__getattr__" | "__dir__")
                || counts.get(local.unwrap_or(name)) != 1
            {
                continue;
            }
            if (style == "all" && local == Some(name))
                || (style == "alias" && local.is_none() && exports.contains(name))
            {
                found.push(alias);
            }
        }
    }
    found
}

/// Safe: a same-name alias binds the same object and only marks the export.
fn alias_fix(parsed: &ParsedSource, alias: &Alias) -> Value {
    let name = alias.name.as_str();
    fix(
        "safe",
        &format!("Re-export {name} as {name}"),
        vec![
            parsed
                .text
                .insert(usize::from(alias.end()), &format!(" as {name}")),
        ],
    )
}

/// Drop the same-name alias and make sure `__all__` lists the name.
///
/// Safe when `__all__` already lists it. Otherwise every mismatched name joins
/// `__all__` in one edit, which widens or narrows what star imports export.
fn all_fix(parsed: &ParsedSource, alias: &Alias, mismatched: &[&Alias]) -> Option<Value> {
    let module = parsed.module();
    let text = &parsed.text;
    let exports = explicit_exports(module)?;
    let name = alias.name.as_str();
    let start = usize::from(alias.start());
    let end = usize::from(alias.end());
    // Python advances by characters of the normalized name.
    let name_end = text.text[start..]
        .char_indices()
        .nth(name.chars().count())
        .map_or(text.text.len(), |(index, _)| start + index);
    let mut edits = vec![text.edit(name_end, end, "")];
    if exports.contains(name) {
        return Some(fix(
            "safe",
            &format!("Import {name} without the alias"),
            edits,
        ));
    }
    let mut seen = HashSet::new();
    let missing: Vec<&str> = mismatched
        .iter()
        .map(|item| item.name.as_str())
        .filter(|item| !exports.contains(item) && seen.insert(*item))
        .collect();
    edits.push(export_edit(parsed, &missing));
    Some(fix(
        "unsafe",
        &format!("Import {name} without the alias and list it in __all__"),
        edits,
    ))
}

/// Append `names` to the static `__all__`, or create one after the imports.
fn export_edit(parsed: &ParsedSource, names: &[&str]) -> Value {
    let text = &parsed.text;
    let Some(value) = parsed.module().body.iter().rev().find_map(export_value) else {
        let listed: Vec<String> = names.iter().map(|name| format!("\"{name}\"")).collect();
        return text.insert_line(
            import_insertion(parsed),
            &format!("\n__all__ = [{}]", listed.join(", ")),
        );
    };
    let elements = match value {
        Expr::List(node) => &node.elts,
        Expr::Tuple(node) => &node.elts,
        Expr::Set(node) => &node.elts,
        _ => unreachable!("export_value only returns sequences"),
    };
    let quote = elements
        .first()
        .and_then(|first| text.segment(first.range()).chars().next())
        .unwrap_or('"');
    let listed = names
        .iter()
        .map(|name| format!("{quote}{name}{quote}"))
        .collect::<Vec<_>>()
        .join(", ");
    if let Some(last) = elements.last() {
        return text.insert(usize::from(last.end()), &format!(", {listed}"));
    }
    let replacement = match value {
        Expr::Tuple(_) => format!("({listed},)"),
        Expr::Set(_) => format!("{{{listed}}}"),
        _ => format!("[{listed}]"),
    };
    text.replace(value.range(), &replacement)
}

fn export_value(statement: &Stmt) -> Option<&Expr> {
    let (targets, value): (&[Expr], &Expr) = match statement {
        Stmt::Assign(node) => (&node.targets, &node.value),
        Stmt::AnnAssign(node) => (
            std::slice::from_ref(node.target.as_ref()),
            node.value.as_deref()?,
        ),
        Stmt::AugAssign(node) => (std::slice::from_ref(node.target.as_ref()), &node.value),
        _ => return None,
    };
    let is_sequence = matches!(value, Expr::List(_) | Expr::Tuple(_) | Expr::Set(_));
    let names_all = targets
        .iter()
        .any(|target| matches!(target, Expr::Name(name) if name.id.as_str() == "__all__"));
    (is_sequence && names_all).then_some(value)
}
