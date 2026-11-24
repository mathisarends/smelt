//! ARC014: packages missing a module or entry point their contract requires.
use super::{StructureContext, StructureFinding};
use crate::checker::patterns::ModulePattern;
use serde_json::json;
use std::collections::HashSet;

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let files = context.files;
    let known: HashSet<&str> = files
        .sources
        .keys()
        .chain(&files.packages)
        .map(String::as_str)
        .collect();
    let mut found = Vec::new();
    for contract in &context.contracts.resolved {
        let missing: Vec<String> = contract
            .spec
            .required
            .iter()
            .map(|entry| contract.qualify(entry))
            .filter(|module| !known.contains(module.as_str()))
            .collect();
        let mut absent: Vec<(String, &str)> = missing
            .iter()
            .map(|module| (module.clone(), "required module"))
            .collect();
        for entry in &contract.spec.public {
            let pattern = contract.qualify(entry);
            let Ok(matcher) = ModulePattern::new(&pattern) else {
                continue;
            };
            if !known.iter().any(|module| matcher.matches(module)) && !missing.contains(&pattern) {
                absent.push((pattern, "public entry point"));
            }
        }
        let info = context.model.info(contract.package);
        for (module, kind) in absent {
            found.push(StructureFinding {
                message: format!(
                    "contract \"{}\" on {} is missing {kind} {module}",
                    contract.name, contract.package
                ),
                path: files
                    .path_for_module(contract.package)
                    .unwrap_or(contract.package)
                    .to_owned(),
                source_module: Some(contract.package.to_owned()),
                feature: info.as_ref().and_then(|info| info.feature.clone()),
                layer: info.as_ref().and_then(|info| info.layer.clone()),
                expected: json!({
                    "contract": contract.name,
                    "package": contract.package,
                    "module": module,
                    "kind": kind,
                }),
                hint: format!(
                    "Implement {module} under {}, or narrow architecture.packages.{} to packages \
                     sharing that structure.",
                    contract.package, contract.name
                ),
                target_module: Some(module),
                ..StructureFinding::default()
            });
        }
    }
    found
}
