//! IMP002: modules that import too many project modules.
use super::{GraphContext, skip_import};
use crate::checker::analysis::architecture::ModuleKind;
use crate::checker::analysis::imports::ImportDetail;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::Violation;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};

const DEFAULT_MAX_MODULE_IMPORTS: usize = 20;
const DEFAULT_EXEMPTIONS: [&str; 2] = ["facades", "composition_root"];

pub fn check(rule: &CatalogRule, context: &GraphContext<'_>) -> Vec<Violation> {
    let packages: HashSet<&str> = context.files.packages.iter().map(String::as_str).collect();
    let mut found = Vec::new();
    for source in context.files.sources.values() {
        let limit = context.contracts.threshold(
            &source.module,
            "max_module_imports",
            DEFAULT_MAX_MODULE_IMPORTS,
        );
        let origin = context
            .contracts
            .policy_for(&source.module, "module_import_exemptions");
        let exempt: Vec<&str> = match origin.as_ref().and_then(|item| item.value.as_array()) {
            Some(values) => values.iter().filter_map(Value::as_str).collect(),
            None => DEFAULT_EXEMPTIONS.to_vec(),
        };
        let is_root = context
            .model
            .info(&source.module)
            .is_some_and(|info| info.kind == ModuleKind::CompositionRoot);
        if (source.is_package && exempt.contains(&"facades"))
            || (exempt.contains(&"composition_root") && is_root)
        {
            continue;
        }
        let mut seen = HashSet::new();
        let targets: Vec<&ImportDetail> = context
            .imports
            .imports_of(&source.module)
            .iter()
            .filter(|detail| !detail.external && !skip_import(context, detail))
            .filter(|detail| seen.insert(detail.imported.as_str()))
            .collect();
        if targets.len() <= limit {
            continue;
        }
        let Some(first) = targets
            .iter()
            .min_by_key(|detail| (detail.line, detail.column))
        else {
            continue;
        };
        let mut names: Vec<String> = targets.iter().map(|d| d.imported.clone()).collect();
        names.sort();
        let mut grouped: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for target in &names {
            let package = if packages.contains(target.as_str()) {
                target.as_str()
            } else {
                target.rsplit_once('.').map_or("", |(parent, _)| parent)
            };
            grouped
                .entry(package.to_owned())
                .or_default()
                .push(target.clone());
        }
        let mut hint = "Split cohesive responsibilities or review deliberate package facades and \
                        their import cycles."
            .to_owned();
        let mut dominant: Option<(&String, usize)> = None;
        for (package, members) in &grouped {
            if dominant.is_none_or(|(_, size)| members.len() > size) {
                dominant = Some((package, members.len()));
            }
        }
        if let Some((package, size)) = dominant
            && size * 2 > targets.len()
        {
            hint.push_str(&format!(
                " IMP001 can review per-package fanout into {package}."
            ));
        }
        let edges: Vec<String> = names
            .iter()
            .map(|target| format!("{} -> {target}", source.module))
            .collect();
        found.push(Violation {
            code: rule.code.clone(),
            rule: rule.name.clone(),
            category: rule.category.clone(),
            severity: rule.default_severity.clone(),
            message: format!(
                "{} imports {} project modules (limit {limit}); review its responsibilities or \
                 package facades",
                source.module,
                targets.len()
            ),
            path: source.path.clone(),
            line: first.line,
            column: Some(first.column),
            end_line: Some(first.line),
            end_column: Some(first.end_column),
            source_module: Some(source.module.clone()),
            target_module: None,
            import_chain: Vec::new(),
            feature: None,
            layer: None,
            edge: None,
            expected: json!({
                "max_module_imports": limit,
                "count": targets.len(),
                "modules": names,
                "imports_by_package": grouped,
                "import_edges": edges,
                "automatic_rewrite": false,
            }),
            hint: Some(hint),
            fix: None,
        });
    }
    found
}
