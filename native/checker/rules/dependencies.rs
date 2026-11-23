//! Rules over the resolved import graph.
mod boundaries;
mod cycles;
mod fanout;

use crate::checker::analysis::architecture::{ArchitectureModel, ModuleInfo};
use crate::checker::analysis::imports::{ImportDetail, ImportIndex};
use crate::checker::contracts::Contracts;
use crate::checker::files::FileIndex;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::{ImportLink, Violation};
use serde_json::Value;
use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

pub const IMPORT_RULES: [&str; 7] = [
    "ARC001", "ARC002", "ARC003", "ARC004", "ARC005", "ARC006", "IMP002",
];

/// Winner and the codes it makes redundant when both report the same root cause.
const SPECIFICITY: [(&str, [&str; 2]); 2] = [
    ("ARC013", ["ARC011", "ARC012"]),
    // Shared or central code importing a feature: the feature import is the cause.
    ("ARC005", ["ARC001", "ARC006"]),
];

/// The project-wide facts the import rules read.
pub struct GraphContext<'a> {
    pub imports: &'a ImportIndex,
    pub model: &'a ArchitectureModel<'a>,
    pub files: &'a FileIndex,
    pub contracts: &'a Contracts<'a>,
}

/// A direct import or, when enabled, a reachable dependency with its chain.
struct BoundaryImport<'a> {
    detail: Cow<'a, ImportDetail>,
    chain: Vec<ImportLink>,
}

/// The parts of an import finding that differ between rules.
struct ImportFinding {
    message: String,
    expected: Value,
    hint: Option<String>,
    chain: Vec<ImportLink>,
    edge: Option<String>,
}

/// Every finding of `rules` at its resolved severity, without redundant ones.
pub fn findings<'r>(
    rules: &[&'r CatalogRule],
    context: &GraphContext<'_>,
    severity: impl Fn(&'r CatalogRule, &str) -> Option<&'r str>,
) -> Vec<Violation> {
    let boundary = boundary_imports(context);
    let mut found = Vec::new();
    for rule in rules {
        let raised = match rule.code.as_str() {
            "ARC001" => boundaries::check_layers(rule, context, &boundary),
            "ARC002" => boundaries::check_cross_feature(rule, context, &boundary),
            "ARC003" => boundaries::check_third_party(rule, context, &boundary),
            "ARC004" => cycles::check(rule, context),
            "ARC005" => boundaries::check_shared(rule, context, &boundary),
            "ARC006" => boundaries::check_composition_root(rule, context, &boundary),
            "IMP002" => fanout::check(rule, context),
            _ => Vec::new(),
        };
        for mut violation in raised {
            let module = violation.source_module.as_deref().unwrap_or_default();
            if let Some(level) = severity(rule, module) {
                level.clone_into(&mut violation.severity);
                found.push(violation);
            }
        }
    }
    deduplicate(found)
}

fn skip_import(context: &GraphContext<'_>, detail: &ImportDetail) -> bool {
    detail.type_checking && !context.model.config.import_graph.include_type_checking
}

fn boundary_imports<'a>(context: &GraphContext<'a>) -> Vec<BoundaryImport<'a>> {
    let mut result: Vec<BoundaryImport<'a>> = context
        .imports
        .all_imports()
        .map(|detail| BoundaryImport {
            detail: Cow::Borrowed(detail),
            chain: Vec::new(),
        })
        .collect();
    let policy = &context.model.config.import_graph;
    if !policy.transitive {
        return result;
    }
    for module in context.imports.modules() {
        let mut seen: HashSet<(&str, &[String])> = context
            .imports
            .imports_of(module)
            .iter()
            .filter(|detail| !skip_import(context, detail))
            .map(|detail| (detail.imported.as_str(), detail.names.as_slice()))
            .collect();
        for path in context
            .imports
            .shortest_chains_from(module, policy.include_type_checking, None)
        {
            let (Some(first), Some(target)) = (path.first(), path.last()) else {
                continue;
            };
            let key = (target.imported.as_str(), target.names.as_slice());
            if path.len() == 1 || !seen.insert(key) {
                continue;
            }
            result.push(BoundaryImport {
                detail: Cow::Owned(ImportDetail {
                    imported: target.imported.clone(),
                    names: target.names.clone(),
                    external: target.external,
                    ..(*first).clone()
                }),
                chain: path
                    .iter()
                    .map(|hop| ImportLink {
                        importer: hop.importer.clone(),
                        imported: hop.imported.clone(),
                        line: Some(hop.line),
                    })
                    .collect(),
            });
        }
    }
    result
}

/// A feature or package initializer may export its own provider module.
fn wiring_facade(model: &ArchitectureModel<'_>, source: &ModuleInfo, target: &ModuleInfo) -> bool {
    if !target.wiring {
        return false;
    }
    let parent = target
        .name
        .rsplit_once('.')
        .map_or(target.name.as_str(), |(parent, _)| parent);
    source.name == parent
        || target.feature.as_ref().is_some_and(|feature| {
            source.feature.as_ref() == Some(feature)
                && source.name == model.feature_package_for(feature)
        })
}

fn import_violation(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
    detail: &ImportDetail,
    finding: ImportFinding,
) -> Violation {
    let info = context.model.info(&detail.importer);
    let target_module = match finding.chain.last() {
        Some(link) if rule.code != "ARC004" => link.imported.clone(),
        _ => detail.imported.clone(),
    };
    let edge = finding.edge.unwrap_or_else(|| {
        let target = if detail.external {
            detail.top_level().to_owned()
        } else {
            context.model.node(&target_module)
        };
        format!("{} -> {target}", context.model.node(&detail.importer))
    });
    Violation {
        code: rule.code.clone(),
        rule: rule.name.clone(),
        category: rule.category.clone(),
        severity: rule.default_severity.clone(),
        message: finding.message,
        path: context.files.sources[&detail.importer].path.clone(),
        line: Some(detail.line),
        column: Some(detail.column),
        end_line: Some(detail.line),
        end_column: Some(detail.end_column),
        source_module: Some(detail.importer.clone()),
        target_module: Some(target_module),
        import_chain: finding.chain,
        feature: info.as_ref().and_then(|info| info.feature.clone()),
        layer: info.as_ref().and_then(|info| info.layer.clone()),
        edge: Some(edge),
        expected: finding.expected,
        hint: finding.hint,
        fix: None,
    }
}

fn join(values: &[String]) -> String {
    if values.is_empty() {
        "(nothing)".to_owned()
    } else {
        values.join(", ")
    }
}

/// Drop findings that a more specific rule already reports for the same cause.
fn deduplicate(violations: Vec<Violation>) -> Vec<Violation> {
    type Cause<'a> = (&'a str, Option<&'a str>, Option<usize>);
    fn key(violation: &Violation) -> Cause<'_> {
        match violation.target_module.as_deref().filter(|t| !t.is_empty()) {
            Some(target) => (&violation.path, Some(target), None),
            None => (&violation.path, None, violation.line),
        }
    }
    let redundant: Vec<bool> = {
        let mut codes: HashMap<Cause<'_>, HashSet<&str>> = HashMap::new();
        for violation in &violations {
            codes
                .entry(key(violation))
                .or_default()
                .insert(&violation.code);
        }
        violations
            .iter()
            .map(|violation| {
                let same_cause = &codes[&key(violation)];
                SPECIFICITY.iter().any(|(winner, losers)| {
                    losers.contains(&violation.code.as_str()) && same_cause.contains(winner)
                })
            })
            .collect()
    };
    violations
        .into_iter()
        .zip(redundant)
        .filter_map(|(violation, drop)| (!drop).then_some(violation))
        .collect()
}
