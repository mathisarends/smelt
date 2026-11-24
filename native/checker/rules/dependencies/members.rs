//! ARC009 member boundaries and ARC010 undeclared member dependencies.
use super::{GraphContext, ImportFinding, import_violation, skip_import};
use crate::checker::analysis::imports::ImportDetail;
use crate::checker::analysis::workspace::WorkspaceIndex;
use crate::checker::patterns::PathPattern;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::Violation;
use serde_json::json;
use std::collections::BTreeSet;

/// ARC009: a member imports a member outside its `may_depend_on` allowlist.
pub fn check_boundaries(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
) -> Result<Vec<Violation>, String> {
    let Some(workspace) = context.workspace()? else {
        return Ok(Vec::new());
    };
    let mut found = Vec::new();
    for (detail, source, target) in member_imports(context, workspace) {
        let Some(allowed) = boundary_restriction(context, source, target)? else {
            continue;
        };
        found.push(import_violation(
            rule,
            context,
            detail,
            ImportFinding {
                message: format!("member {source} must not depend on {target}"),
                expected: json!({
                    "source_member": source,
                    "target_member": target,
                    "may_depend_on": allowed,
                }),
                hint: Some(
                    "Move this dependency behind an allowed member boundary; the workspace \
                     root declares architecture.members."
                        .to_owned(),
                ),
                chain: Vec::new(),
                edge: Some(format!("{source} -> {target}")),
            },
        ));
    }
    Ok(found)
}

/// ARC010: a member imports another member without declaring a project dependency.
pub fn check_declarations(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
) -> Result<Vec<Violation>, String> {
    let Some(workspace) = context.workspace()? else {
        return Ok(Vec::new());
    };
    let mut found = Vec::new();
    for (detail, source, target) in member_imports(context, workspace) {
        let Some(declared) = workspace.dependencies.get(source) else {
            continue;
        };
        let Some(name) = workspace
            .project(target)
            .and_then(|project| project.get("name"))
            .and_then(toml::Value::as_str)
        else {
            continue;
        };
        if declared.contains(target) {
            continue;
        }
        let hint = if boundary_restriction(context, source, target)?.is_some() {
            "Remove this import or move the dependency behind an allowed member boundary. The \
             root architecture.members policy forbids this dependency (ARC009)."
                .to_owned()
        } else {
            format!(
                "Declare {name} in {source}/pyproject.toml project.dependencies and configure \
                 its tool.uv.sources workspace source."
            )
        };
        found.push(import_violation(
            rule,
            context,
            detail,
            ImportFinding {
                message: format!(
                    "member {source} imports {target} without a declared dependency on {name}"
                ),
                expected: json!({
                    "source_member": source,
                    "target_member": target,
                    "dependency": name,
                    "declared_members": declared,
                }),
                hint: Some(hint),
                chain: Vec::new(),
                edge: Some(format!("{source} -> {target}")),
            },
        ));
    }
    Ok(found)
}

/// The applicable allowlist when it rejects the dependency.
fn boundary_restriction(
    context: &GraphContext<'_>,
    source: &str,
    target: &str,
) -> Result<Option<Vec<String>>, String> {
    let mut applies = false;
    let mut allowed = BTreeSet::new();
    for (pattern, may_depend_on) in &context.model.config.members {
        if PathPattern::new(pattern)?.matches(source) {
            applies = true;
            allowed.extend(may_depend_on.iter().cloned());
        }
    }
    if !applies {
        return Ok(None);
    }
    for pattern in &allowed {
        if PathPattern::new(pattern)?.matches(target) {
            return Ok(None);
        }
    }
    Ok(Some(allowed.into_iter().collect()))
}

/// Direct production imports between two different members.
fn member_imports<'c>(
    context: &'c GraphContext<'_>,
    workspace: &'c WorkspaceIndex,
) -> Vec<(&'c ImportDetail, &'c str, &'c str)> {
    context
        .imports
        .all_imports()
        .filter(|detail| !detail.external && !skip_import(context, detail))
        .filter_map(|detail| {
            let source = workspace.member_for_module(context.files, &detail.importer)?;
            let target = workspace.member_for_module(context.files, &detail.imported)?;
            (source != target).then_some((detail, source, target))
        })
        .collect()
}
