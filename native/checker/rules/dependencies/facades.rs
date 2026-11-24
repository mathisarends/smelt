//! ARC008: public implementation names with many outside importers but no facade export.
use super::GraphContext;
use crate::checker::contracts::FacadePolicy;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::Violation;
use serde_json::json;

const IMPORTER_PREVIEW: usize = 4;

pub fn check(rule: &CatalogRule, context: &GraphContext<'_>) -> Result<Vec<Violation>, String> {
    let contracts = context.contracts.facade_policies(FacadePolicy::Export)?;
    if contracts.is_empty() {
        return Ok(Vec::new());
    }
    let usage = context.facade_usage();
    let (ownership, _) = usage.canonical_exports(context.canonical_exports);
    let mut found = Vec::new();
    for contract in contracts {
        let Some(threshold) = contract.spec.min_importers_to_export else {
            continue;
        };
        if context.contracts.resolved.iter().any(|boundary| {
            boundary.package == contract.package && !boundary.exposes(contract.package)
        }) {
            continue;
        }
        let package = contract.package;
        for gap in usage.gaps(package, contract.spec.count_tests) {
            let mut importers: Vec<&str> = gap
                .source_importers
                .iter()
                .chain(&gap.test_importers)
                .map(String::as_str)
                .collect();
            importers.sort_unstable();
            if importers.len() < threshold {
                continue;
            }
            let qualified = format!("{}.{}", gap.module, gap.name);
            if ownership.contains_key(&qualified) {
                continue;
            }
            let mut preview = importers
                .iter()
                .take(IMPORTER_PREVIEW)
                .copied()
                .collect::<Vec<_>>()
                .join(", ");
            if importers.len() > IMPORTER_PREVIEW {
                preview.push_str(&format!(", … (+{})", importers.len() - IMPORTER_PREVIEW));
            }
            let cycle = if gap.cycle.is_empty() {
                "no return path found in the analyzed explicit runtime import graph; \
                 initialization safety is unproven"
                    .to_owned()
            } else {
                gap.cycle.join(" -> ")
            };
            let relative = gap
                .module
                .strip_prefix(&format!("{package}."))
                .unwrap_or(&gap.module);
            let suggested_import = format!("from .{relative} import {}", gap.name);
            let mut repair = format!("Review exporting {} from {package}", gap.name);
            if gap.needs_import {
                repair.push_str(&format!(
                    " with `{suggested_import}`; update __all__ if declared"
                ));
            } else {
                repair.push_str(" by adding it to __all__");
            }
            let mut hint = format!("{repair}. Importers: {preview}. Cycle check: {cycle}.");
            if !gap.reexports.is_empty() {
                hint.push_str(&format!(
                    " Existing exports: {}. Review whether one is already the canonical API \
                     before adding another export.",
                    gap.reexports.join(", ")
                ));
            }
            let root = gap.module.split('.').next().unwrap_or_default();
            let (same_root, other_root): (Vec<&String>, Vec<&String>) = gap
                .source_importers
                .iter()
                .partition(|caller| caller.split('.').next() == Some(root));
            found.push(Violation {
                code: rule.code.clone(),
                rule: rule.name.clone(),
                category: rule.category.clone(),
                severity: rule.default_severity.clone(),
                message: format!(
                    "{qualified} has {} outside importers but is not exported by {package}",
                    importers.len()
                ),
                path: gap.path.clone(),
                line: Some(gap.line),
                column: Some(gap.column),
                end_line: Some(gap.line),
                end_column: None,
                source_module: Some(gap.module.clone()),
                target_module: Some(package.to_owned()),
                import_chain: Vec::new(),
                feature: None,
                layer: None,
                edge: Some(format!("{qualified} -> {package}")),
                expected: json!({
                    "contract": contract.name,
                    "package": package,
                    "name": gap.name,
                    "definition": qualified,
                    "min_importers_to_export": threshold,
                    "count_tests": contract.spec.count_tests,
                    "source_importers": gap.source_importers,
                    "same_root_importers": same_root,
                    "other_root_importers": other_root,
                    "test_importers": gap.test_importers,
                    "count": importers.len(),
                    "reexports": gap.reexports,
                    "cycle_check": if gap.cycle.is_empty() { "no-cycle-found" } else { "cycle" },
                    "cycle": gap.cycle,
                    "needs_import": gap.needs_import,
                    "automatic_rewrite": false,
                    "suggested_import": gap.needs_import.then_some(&suggested_import),
                    "export_edges": importers
                        .iter()
                        .map(|caller| format!("{package}:{qualified} <- {caller}"))
                        .collect::<Vec<_>>(),
                }),
                hint: Some(hint),
                fix: None,
            });
        }
    }
    Ok(found)
}
