//! ARC013: source outside the architecture policy and exclusions without a reason.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::coverage::{Coverage, CoverageInputs, Status};
use serde_json::json;

pub fn check(context: &StructureContext<'_>) -> Result<Vec<StructureFinding>, String> {
    if !context.strict_coverage {
        return Ok(Vec::new());
    }
    let coverage = Coverage::build(
        context.files,
        context.model,
        &CoverageInputs {
            project: context.project,
            workspace_packages: context.workspace_packages,
            sources: context.sources,
        },
    )?;
    let mut found: Vec<StructureFinding> = coverage
        .unexplained_exclusions
        .iter()
        .map(|pattern| StructureFinding {
            message: format!("exclusion \"{pattern}\" has no coverage reason"),
            expected: json!({
                "exclusion": {
                    "path": pattern,
                    "reason": "<why this code is outside this policy>",
                }
            }),
            hint: "Use a project.exclude entry with path and a nonempty reason.".to_owned(),
            ..StructureFinding::default()
        })
        .collect();
    for entry in coverage.gaps() {
        let uncovered = entry.status == Status::Uncovered;
        let mut package = entry
            .module
            .as_deref()
            .map(|module| module.split('.').next().unwrap_or(module));
        let standalone = entry
            .module
            .as_deref()
            .is_some_and(|module| !module.contains('.'))
            && !entry.path.ends_with("__init__.py");
        let mut hint = if uncovered {
            format!(
                "Select {} under project.root_packages and {} under project.source_roots, then \
                 declare its architecture role.",
                package.unwrap_or("the package"),
                entry.source_root.as_deref().unwrap_or("its import root"),
            )
        } else {
            "Move the implementation into a declared layer, or assign its intended role in the \
             intended layer's modules list. Reserve wiring for actual assembly."
                .to_owned()
        };
        if uncovered && standalone {
            package = None;
            "Move the standalone module into a governed package; project.root_packages selects \
             directories, not individual Python files."
                .clone_into(&mut hint);
        }
        found.push(StructureFinding {
            message: format!(
                "{} is {}: {}",
                entry.path,
                entry.status.name(),
                entry.reason.as_deref().unwrap_or("None"),
            ),
            path: entry.path.clone(),
            source_module: entry.module.clone(),
            feature: entry.feature.clone(),
            layer: entry.layer.clone(),
            expected: json!({
                "coverage_status": "governed, facade or excluded with a reason",
                "actual_status": entry.status.name(),
                "root_package": package,
                "source_root": entry.source_root,
            }),
            hint: hint
                + " For deliberate exclusions, declare path and reason under project.exclude.",
            ..StructureFinding::default()
        });
    }
    Ok(found)
}
