//! ARC007 private modules of contracted packages and IMP001 package import fan-out.
use super::{GraphContext, ImportFinding, import_violation_at, join_or, skip_import};
use crate::checker::analysis::imports::ImportDetail;
use crate::checker::analysis::namespace::resolve_dotted;
use crate::checker::contracts::{FacadePolicy, ResolvedContract};
use crate::checker::patterns::is_within;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::Violation;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};

/// ARC007: code outside a contracted package imports one of its private modules.
pub fn check_private_modules(rule: &CatalogRule, context: &GraphContext<'_>) -> Vec<Violation> {
    let contracts: Vec<(&ResolvedContract<'_>, Vec<String>)> = context
        .contracts
        .resolved
        .iter()
        .filter_map(|contract| Some((contract, contract.public()?)))
        .collect();
    if contracts.is_empty() {
        return Vec::new();
    }
    let mut found = Vec::new();
    for (path, detail) in context.package_api_imports() {
        if detail.external || skip_import(context, detail) {
            continue;
        }
        for target in module_targets(context, detail) {
            for (contract, public) in &contracts {
                if !is_within(&target, contract.package)
                    || context.inside_contract(path, &detail.importer, contract)
                    || contract.exposes(&target)
                {
                    continue;
                }
                let detail = ImportDetail {
                    imported: target.clone(),
                    ..detail.clone()
                };
                found.push(import_violation_at(
                    rule,
                    context,
                    &detail,
                    path.to_owned(),
                    ImportFinding {
                        message: format!(
                            "{} imports private module {target} of contract \"{}\"",
                            detail.importer, contract.name
                        ),
                        expected: json!({
                            "contract": contract.name,
                            "package": contract.package,
                            "public": public,
                        }),
                        hint: Some(format!(
                            "Use a public entry point of {}: {}.",
                            contract.package,
                            join_or(public, "none; this package is private")
                        )),
                        chain: Vec::new(),
                        edge: Some(format!(
                            "{} -> {} ({})",
                            detail.importer, contract.package, contract.name
                        )),
                    },
                ));
            }
        }
    }
    found
}

/// Keep symbol re-exports public, but resolve explicit module-valued aliases.
fn module_targets(context: &GraphContext<'_>, detail: &ImportDetail) -> Vec<String> {
    let imported = context.bound_module(detail).to_owned();
    let mut seen = HashSet::from([imported.clone()]);
    let mut targets = vec![imported];
    for name in &detail.names {
        if let Some((module, None)) = resolve_dotted(name, context.namespaces())
            && seen.insert(module.clone())
        {
            targets.push(module);
        }
    }
    targets
}

/// IMP001: one caller spreads a package API across several import paths.
pub fn check_bundles(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
) -> Result<Vec<Violation>, String> {
    let mut found = Vec::new();
    for contract in context.contracts.facade_policies(FacadePolicy::Bundle)? {
        let Some(threshold) = contract.spec.min_imports_to_bundle else {
            continue;
        };
        // Each caller's targets keep their first import, in import order.
        let mut callers: BTreeMap<&str, Vec<(&str, &ImportDetail)>> = BTreeMap::new();
        for (path, detail) in context.package_api_imports() {
            if detail.external
                || skip_import(context, detail)
                || context.inside_contract(path, &detail.importer, contract)
            {
                continue;
            }
            let imported = context.bound_module(detail);
            if !is_within(imported, contract.package) {
                continue;
            }
            let targets = callers.entry(path).or_default();
            if !targets.iter().any(|(module, _)| *module == imported) {
                targets.push((imported, detail));
            }
        }
        for (path, targets) in callers {
            if targets.len() < threshold {
                continue;
            }
            let Some((_, first)) = targets
                .iter()
                .min_by_key(|(_, detail)| (detail.line, detail.column))
            else {
                continue;
            };
            let caller = &first.importer;
            let modules: BTreeSet<&str> = targets.iter().map(|(module, _)| *module).collect();
            let evidence = bundle_evidence(context, path, contract.package, &targets);
            let hint = if evidence.cycle.is_empty() {
                format!(
                    "Review deliberate exports from {}, then combine imports in the same scope \
                     and guard. No cycle was found in the explicit runtime graph; review \
                     initialization before changing exports.",
                    contract.package
                )
            } else {
                format!(
                    "Bundling is blocked by a known runtime import cycle: {}",
                    evidence.cycle.join(" -> ")
                )
            };
            let mut expected = json!({
                "contract": contract.name,
                "package": contract.package,
                "min_imports_to_bundle": threshold,
                "modules": modules,
                "count": targets.len(),
            });
            if let (Value::Object(expected), Value::Object(evidence)) =
                (&mut expected, evidence.to_json())
            {
                expected.extend(evidence);
            }
            found.push(import_violation_at(
                rule,
                context,
                first,
                path.to_owned(),
                ImportFinding {
                    message: format!(
                        "{caller} imports {} modules of {}; consider its package facade",
                        targets.len(),
                        contract.package
                    ),
                    expected,
                    hint: Some(hint),
                    chain: Vec::new(),
                    edge: Some(format!(
                        "{caller} -> {} ({})",
                        contract.package, contract.name
                    )),
                },
            ));
        }
    }
    Ok(found)
}

struct BundleEvidence {
    cycle: Vec<String>,
    exports: Vec<Value>,
}

impl BundleEvidence {
    fn to_json(&self) -> Value {
        let blocked = !self.cycle.is_empty();
        json!({
            "cycle_check": if blocked { "cycle" } else { "no-cycle-found" },
            "cycle": self.cycle,
            "proposed_exports": if blocked { Vec::new() } else { self.exports.clone() },
            "blocked_exports": if blocked { self.exports.clone() } else { Vec::new() },
            "automatic_rewrite": false,
            "initialization_safety": "unproven",
        })
    }
}

fn bundle_evidence(
    context: &GraphContext<'_>,
    path: &str,
    package: &str,
    targets: &[(&str, &ImportDetail)],
) -> BundleEvidence {
    let caller = targets
        .first()
        .map(|(_, detail)| detail.importer.as_str())
        .unwrap_or_default();
    let test = context.is_test_path(path);
    let exports = context
        .facade_usage()
        .gaps(package, true)
        .into_iter()
        .filter(|gap| {
            let importers = if test {
                &gap.test_importers
            } else {
                &gap.source_importers
            };
            importers.iter().any(|importer| importer == caller)
        })
        .map(|gap| {
            let relative = gap
                .module
                .strip_prefix(&format!("{package}."))
                .unwrap_or(&gap.module);
            json!({
                "definition": format!("{}.{}", gap.module, gap.name),
                "name": gap.name,
                "suggested_import": format!("from .{relative} import {}", gap.name),
                "needs_import": gap.needs_import,
            })
        })
        .collect();
    let mut adjacency: HashMap<String, BTreeSet<String>> = context
        .first_party_edges(false)
        .into_iter()
        .map(|(module, children)| {
            (
                module.to_owned(),
                children.into_iter().map(str::to_owned).collect(),
            )
        })
        .collect();
    let graph_caller = match context.files.sources.get(caller) {
        Some(source) if source.path == path => caller.to_owned(),
        _ => format!("<test:{path}>"),
    };
    adjacency
        .entry(graph_caller)
        .or_default()
        .insert(package.to_owned());
    adjacency.entry(package.to_owned()).or_default().extend(
        targets
            .iter()
            .map(|(module, _)| *module)
            .filter(|module| *module != package)
            .map(str::to_owned),
    );
    BundleEvidence {
        cycle: shortest_cycle(package, &adjacency).unwrap_or_default(),
        exports,
    }
}

/// The shortest path `start -> ... -> start`, children in sorted order.
fn shortest_cycle(
    start: &str,
    adjacency: &HashMap<String, BTreeSet<String>>,
) -> Option<Vec<String>> {
    let mut previous: HashMap<&str, &str> = HashMap::new();
    let mut queue: VecDeque<&str> = VecDeque::from([start]);
    let mut visited: HashSet<&str> = HashSet::new();
    while let Some(node) = queue.pop_front() {
        for child in adjacency.get(node).into_iter().flatten() {
            if child == start {
                let mut path = vec![node];
                while let Some(last) = path.last().copied()
                    && last != start
                {
                    path.push(previous[last]);
                }
                let mut cycle: Vec<String> = path.into_iter().rev().map(str::to_owned).collect();
                cycle.push(start.to_owned());
                return Some(cycle);
            }
            if visited.insert(child) {
                previous.insert(child, node);
                queue.push_back(child);
            }
        }
    }
    None
}
