//! MOD007: `modeling/prefer-project-base-class`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{Names, import_bindings, qualified_name};
use crate::checker::analysis::nodes::all_classes;
use crate::checker::patterns::is_within;
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, ModModule, Stmt};
use serde_json::json;

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let source = input.source;
    let Some(origin) = input.contracts.policy_for(&source.module, "bases") else {
        return Vec::new();
    };
    let Some(bases) = origin.value.as_object().filter(|bases| !bases.is_empty()) else {
        return Vec::new();
    };
    let module = input.parsed.module();
    let mapping: Vec<(&str, &str)> = bases
        .iter()
        .filter_map(|(outside, project)| Some((outside.as_str(), project.as_str()?)))
        .filter(|(_, project)| !defines(module, &source.module, project))
        .collect();
    let bindings = import_bindings(module, &source.module, source.is_package);
    let mut found = Vec::new();
    for owner in all_classes(module) {
        let Some(arguments) = &owner.arguments else {
            continue;
        };
        for base in &arguments.args {
            let Some(outside) = resolve(base, &bindings) else {
                continue;
            };
            let Some((_, project)) = mapping.iter().find(|(name, _)| *name == outside) else {
                continue;
            };
            let (package, name) = project.rsplit_once('.').unwrap_or(("", project));
            let (line, column) = input.place(input.parsed.class_start(owner));
            found.push(input.violation(Finding {
                message: format!(
                    "Class {} inherits {outside} directly; {} expects {project}",
                    owner.name, origin.label
                ),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "contract": origin.contract,
                    "class": owner.name.as_str(),
                    "outside_base": outside,
                    "project_base": project,
                    "automatic_rewrite": false,
                }),
                hint: format!("Inherit {name} from {package} instead of {outside}."),
                fix: None,
            }));
        }
    }
    found
}

/// The project base may be re-exported, so its package prefix is enough.
fn defines(tree: &ModModule, module: &str, project: &str) -> bool {
    let (package, name) = project.rsplit_once('.').unwrap_or(("", project));
    is_within(module, package)
        && tree
            .body
            .iter()
            .any(|node| matches!(node, Stmt::ClassDef(class) if class.name.as_str() == name))
}

/// Generic bases such as `Model[T]` name their class before the subscript.
fn resolve(base: &Expr, bindings: &Names<'_>) -> Option<String> {
    match base {
        Expr::Subscript(item) => qualified_name(&item.value, bindings),
        other => qualified_name(other, bindings),
    }
}
