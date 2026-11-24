//! Public members whose observed use stays inside their module, class or tests.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::architecture::ModuleKind;
use crate::checker::analysis::members::{AttributeMember, Member, MemberKind, MemberUsageIndex};
use serde_json::json;
use std::collections::HashSet;

/// A private spelling of `name` that `occupied` does not bind yet.
pub(super) fn private_name(name: &str, occupied: &HashSet<String>) -> String {
    let preferred = format!("_{}", name.trim_start_matches('_'));
    if !occupied.contains(&preferred) {
        return preferred;
    }
    let mut candidate = format!("{preferred}_internal");
    let mut suffix = 2;
    while occupied.contains(&candidate) {
        candidate = format!("{preferred}_internal_{suffix}");
        suffix += 1;
    }
    candidate
}

/// Repair wording that names the target only when a collision changes it.
pub(super) fn rename_advice(name: &str, suggested: &str) -> String {
    if suggested == format!("_{}", name.trim_start_matches('_')) {
        return "add a leading underscore".to_owned();
    }
    format!("rename to {suggested} (the underscored name is taken)")
}

pub(super) fn is_composition_root(context: &StructureContext<'_>, module: &str) -> bool {
    context
        .model
        .info(module)
        .is_some_and(|info| info.kind == ModuleKind::CompositionRoot)
}

fn qualified(member: &Member<'_>) -> String {
    match &member.owner {
        Some(owner) => format!("{}.{}", owner.name, member.node.name),
        None => member.node.name.clone(),
    }
}

/// VIS001: a public function or method used only inside its module or class.
pub fn check_internal_members(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let usage = context.member_usage();
    let scope = MemberUsageIndex::usage_scope(context.project);
    let mut found = Vec::new();
    for member in usage.members() {
        let module = member.source.module.as_str();
        if is_composition_root(context, module) {
            continue;
        }
        let node = &member.node;
        if node.name.starts_with('_')
            || member.kind == MemberKind::StaticMethod
            || member.external_use
        {
            continue;
        }
        let qualified = qualified(member);
        let suggested = private_name(&node.name, &member.occupied_names);
        let place = if member.owner.is_some() {
            "class"
        } else {
            "module"
        };
        found.push(StructureFinding {
            message: format!("{qualified} is public, but has no observed use outside its {place}"),
            path: member.source.path.clone(),
            position: Some((node.line, node.column)),
            source_module: Some(module.to_owned()),
            target_module: Some(format!("{module}.{qualified}")),
            expected: json!({
                "kind": member.kind.as_str(),
                "class": member.owner.as_ref().map(|owner| owner.name.clone()),
                "member": node.name,
                "suggested_name": suggested,
                "usage_scope": scope,
                "observed_usage": if member.internal_references.is_empty() { "unused" } else { "internal-only" },
                "internal_references": member.internal_references,
                "automatic_rename": false,
            }),
            hint: format!(
                "Unless public by design, {}; otherwise export it or add `# smelt: ignore[VIS001] -- <reason>` on the definition.",
                rename_advice(&node.name, &suggested)
            ),
            ..StructureFinding::default()
        });
    }
    found
}

/// LAY005: an internal static method that could be a private module function.
pub fn check_static_helpers(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let usage = context.member_usage();
    let scope = MemberUsageIndex::usage_scope(context.project);
    let mut found = Vec::new();
    for member in usage.members() {
        if member.kind != MemberKind::StaticMethod
            || member.external_use
            || member.class_dependencies
            || member.node.name.starts_with("__")
        {
            continue;
        }
        let node = &member.node;
        let module = member.source.module.as_str();
        let owner = member
            .owner
            .as_ref()
            .map_or("", |owner| owner.name.as_str());
        let name = private_name(&node.name, &member.module_names);
        found.push(StructureFinding {
            message: format!(
                "{owner}.{} is an internal static helper; prefer a private module function",
                node.name
            ),
            path: member.source.path.clone(),
            position: Some((node.line, node.column)),
            source_module: Some(module.to_owned()),
            target_module: Some(format!("{module}.{owner}.{}", node.name)),
            expected: json!({
                "class": member.owner.as_ref().map(|owner| owner.name.clone()),
                "method": node.name,
                "suggested_name": name,
                "usage_scope": scope,
                "destination": module,
                "internal_references": member.internal_references,
                "automatic_extraction": false,
            }),
            hint: format!(
                "Review moving this helper to {name} at module scope, preserving defaults, annotations and deliberate public APIs."
            ),
            ..StructureFinding::default()
        });
    }
    found
}

fn boundary_for<'c>(context: &'c StructureContext<'_>) -> impl Fn(&str) -> String + 'c {
    move |module: &str| {
        context
            .contracts
            .policy_for(module, "public_attribute_boundary")
            .and_then(|origin| origin.value.as_str())
            .unwrap_or("class")
            .to_owned()
    }
}

/// VIS004: a public data attribute without access outside its boundary.
pub fn check_internal_attributes(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let usage = context.member_usage();
    let scope = MemberUsageIndex::usage_scope(context.project);
    let boundary_for = boundary_for(context);
    let mut found = Vec::new();
    for attribute in usage.internal_attributes(&boundary_for) {
        let suggested = private_name(&attribute.name, &attribute.occupied_names);
        let boundary = boundary_for(&attribute.source.module);
        let mut repair = rename_advice(&attribute.name, &suggested);
        if boundary == "module" {
            repair += " and update same-module reads and writes";
        }
        found.push(StructureFinding {
            message: format!(
                "{} is public, but has no observed access outside its {boundary}",
                attribute_name(attribute)
            ),
            expected: json!({
                "class": attribute.owner.name,
                "attribute": attribute.name,
                "kind": attribute.kind,
                "suggested_name": suggested,
                "boundary": boundary,
                "usage_scope": scope,
                "internal_references": attribute.internal_references,
                "automatic_rename": false,
            }),
            hint: format!(
                "Unless public by design, {repair}. Preserve deliberate public contracts with a reasoned VIS004 suppression on the declaration."
            ),
            ..attribute_finding(attribute)
        });
    }
    found
}

fn attribute_name(attribute: &AttributeMember<'_>) -> String {
    format!("{}.{}", attribute.owner.name, attribute.name)
}

fn attribute_finding(attribute: &AttributeMember<'_>) -> StructureFinding {
    let module = &attribute.source.module;
    StructureFinding {
        path: attribute.source.path.clone(),
        position: Some((attribute.node.line, attribute.node.column)),
        source_module: Some(module.clone()),
        target_module: Some(format!("{module}.{}", attribute_name(attribute))),
        ..StructureFinding::default()
    }
}

fn test_references(references: &[(String, usize)]) -> serde_json::Value {
    references
        .iter()
        .map(|(path, line)| json!({"path": path, "line": line}))
        .collect()
}

/// VIS009: a public function only tests use outside its module.
pub fn check_test_only_functions(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let usage = context.member_usage();
    let scope = MemberUsageIndex::usage_scope(context.project);
    let mut found = Vec::new();
    for (function, references) in usage.test_only_functions(context.test_modules()) {
        let node = &function.node;
        let module = &function.source.module;
        let suggested = private_name(&node.name, &function.occupied_names);
        let internal = !function.internal_references.is_empty();
        let observed = if internal {
            "among analyzed modules, only tests access it outside its defining module"
        } else {
            "unused outside tests among analyzed modules"
        };
        let advice = rename_advice(&node.name, &suggested);
        found.push(StructureFinding {
            message: format!("{module}.{} is public; {observed}", node.name),
            path: function.source.path.clone(),
            position: Some((node.line, 0)),
            column_less: true,
            source_module: Some(module.clone()),
            expected: json!({
                "function": node.name,
                "suggested_name": suggested,
                "usage_scope": scope,
                "observed_usage": if internal { "internal-and-tests" } else { "tests-only" },
                "internal_references": function.internal_references,
                "test_references": test_references(&references),
            }),
            hint: if internal {
                format!("Unless public by design, {advice} and test its public caller instead.")
            } else {
                format!("Unless public by design, remove it or {advice}.")
            },
            ..StructureFinding::default()
        });
    }
    found
}

/// VIS010: a public attribute only tests read outside its boundary.
pub fn check_test_only_attributes(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let usage = context.member_usage();
    let scope = MemberUsageIndex::usage_scope(context.project);
    let boundary_for = boundary_for(context);
    let mut found = Vec::new();
    for (attribute, references) in usage.test_only_attributes(context.test_modules(), &boundary_for)
    {
        let suggested = private_name(&attribute.name, &attribute.occupied_names);
        let boundary = boundary_for(&attribute.source.module);
        found.push(StructureFinding {
            message: format!(
                "{} is public; among analyzed modules, only tests read it outside its defining {boundary}",
                attribute_name(attribute)
            ),
            expected: json!({
                "class": attribute.owner.name,
                "attribute": attribute.name,
                "kind": attribute.kind,
                "suggested_name": suggested,
                "boundary": boundary,
                "usage_scope": scope,
                "internal_references": attribute.internal_references,
                "test_references": test_references(&references),
                "automatic_rename": false,
            }),
            hint: format!(
                "Unless public by design, {} and test public behavior instead.",
                rename_advice(&attribute.name, &suggested)
            ),
            ..attribute_finding(attribute)
        });
    }
    found
}
