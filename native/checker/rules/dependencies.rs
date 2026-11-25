//! Rules over the resolved import graph.
mod boundaries;
mod contracts;
mod cycles;
mod facades;
mod fanout;
mod members;

use crate::checker::analysis::architecture::{ArchitectureModel, ModuleInfo};
use crate::checker::analysis::facade_usage::FacadeUsage;
use crate::checker::analysis::imports::{ImportDetail, ImportIndex};
use crate::checker::analysis::mirror::Mirroring;
use crate::checker::analysis::namespace::{Namespace, project_namespaces, resolve_dotted};
use crate::checker::analysis::workspace::WorkspaceIndex;
use crate::checker::config::TestPolicy;
use crate::checker::contracts::{Contracts, ResolvedContract};
use crate::checker::files::{FileIndex, SourceFile};
use crate::checker::patterns::is_within;
use crate::checker::profile;
use crate::checker::settings::CatalogRule;
use crate::checker::source::ParsedSource;
use crate::checker::violation::{ImportLink, Violation};
use serde_json::Value;
use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::sync::OnceLock;

pub const IMPORT_RULES: [&str; 12] = [
    "ARC001", "ARC002", "ARC003", "ARC004", "ARC005", "ARC006", "ARC007", "ARC008", "ARC009",
    "ARC010", "IMP001", "IMP002",
];

/// The import rules that read the project imports of discovered tests.
pub const TEST_IMPORT_RULES: [&str; 3] = ["ARC007", "ARC008", "IMP001"];

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
    pub sources: &'a [(&'a SourceFile, &'a ParsedSource)],
    pub tests: &'a [(&'a SourceFile, &'a ParsedSource)],
    pub test_roots: &'a [String],
    pub test_policy: &'a TestPolicy,
    pub root_packages: &'a [String],
    pub canonical_exports: &'a BTreeMap<String, String>,
    pub namespaces: OnceLock<HashMap<&'a str, Namespace>>,
    pub facade_usage: OnceLock<FacadeUsage<'a>>,
    /// `project.workspace: uv`.
    pub uv_workspace: bool,
    pub workspace: OnceLock<Result<WorkspaceIndex, String>>,
}

impl<'a> GraphContext<'a> {
    fn namespaces(&self) -> &HashMap<&'a str, Namespace> {
        self.namespaces
            .get_or_init(|| project_namespaces(self.sources, &self.files.packages))
    }

    fn facade_usage(&self) -> &FacadeUsage<'a> {
        self.facade_usage.get_or_init(|| {
            FacadeUsage::new(
                self.files,
                self.sources,
                self.tests,
                self.test_roots,
                self.first_party_edges(false),
            )
        })
    }

    /// The uv workspace members, or `None` outside a uv workspace.
    fn workspace(&self) -> Result<Option<&WorkspaceIndex>, String> {
        if !self.uv_workspace {
            return Ok(None);
        }
        self.workspace
            .get_or_init(|| WorkspaceIndex::new(self.files))
            .as_ref()
            .map(Some)
            .map_err(Clone::clone)
    }

    /// Imports between analyzed first-party modules, by importer.
    fn first_party_edges(
        &self,
        include_type_checking: bool,
    ) -> HashMap<&'a str, BTreeSet<&'a str>> {
        let mut edges: HashMap<&'a str, BTreeSet<&'a str>> = HashMap::new();
        for detail in self.imports.all_imports() {
            if detail.external || (detail.type_checking && !include_type_checking) {
                continue;
            }
            edges
                .entry(detail.importer.as_str())
                .or_default()
                .insert(detail.imported.as_str());
        }
        edges
    }

    /// Source and test callers shared by all package API policies, with their paths.
    fn package_api_imports(&self) -> impl Iterator<Item = (&'a str, &'a ImportDetail)> {
        let sources = &self.files.sources;
        self.imports
            .all_imports()
            .map(|detail| (sources[&detail.importer].path.as_str(), detail))
            .chain(
                self.imports
                    .imports_in_tests()
                    .iter()
                    .map(|(path, detail)| (path.as_str(), detail)),
            )
    }

    fn is_test_path(&self, path: &str) -> bool {
        self.files.is_test_path(path, self.test_roots)
    }

    /// Whether the import in `path` comes from inside the contracted package.
    ///
    /// Production code inside the package is. Tests are outside callers, unless the
    /// contract sets `test_access: mirrored` and the test mirrors one of its modules.
    fn inside_contract(&self, path: &str, importer: &str, contract: &ResolvedContract<'_>) -> bool {
        if !self.is_test_path(path) {
            return is_within(importer, contract.package);
        }
        contract.spec.mirrored_test_access
            && Mirroring::new(self.files, self.test_policy, self.root_packages)
                .mirrored_modules(path)
                .iter()
                .any(|module| is_within(module, contract.package))
    }

    /// The module whose attribute a `from` import binds.
    ///
    /// The graph resolves `from app.billing import invoice` to the submodule
    /// `app.billing.invoice` whenever one exists. When the facade binds `invoice` to
    /// another object, as `from .invoice import invoice` does, the statement reads that
    /// attribute of `app.billing` instead.
    fn bound_module<'d>(&self, detail: &'d ImportDetail) -> &'d str {
        let Some((package, _)) = detail.imported.rsplit_once('.') else {
            return &detail.imported;
        };
        if !detail.names.contains(&detail.imported)
            || !detail.line_contents.trim_start().starts_with("from ")
        {
            return &detail.imported;
        }
        match resolve_dotted(&detail.imported, self.namespaces()) {
            Some((_, Some(_))) => package,
            _ => &detail.imported,
        }
    }
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

/// Every finding of `rules` at its resolved severity.
pub fn findings<'r>(
    rules: &[&'r CatalogRule],
    context: &GraphContext<'_>,
    severity: impl Fn(&'r CatalogRule, &str, &str) -> Option<&'r str>,
) -> Result<Vec<Violation>, String> {
    let boundary = boundary_imports(context);
    let mut found = Vec::new();
    for rule in rules {
        let _span = profile::span(format!("rule {}", rule.code));
        let raised = match rule.code.as_str() {
            "ARC001" => boundaries::check_layers(rule, context, &boundary),
            "ARC002" => boundaries::check_cross_feature(rule, context, &boundary),
            "ARC003" => boundaries::check_third_party(rule, context, &boundary),
            "ARC004" => cycles::check(rule, context),
            "ARC005" => boundaries::check_shared(rule, context, &boundary),
            "ARC006" => boundaries::check_composition_root(rule, context, &boundary),
            "ARC007" => contracts::check_private_modules(rule, context),
            "ARC008" => facades::check(rule, context)?,
            "ARC009" => members::check_boundaries(rule, context)?,
            "ARC010" => members::check_declarations(rule, context)?,
            "IMP001" => contracts::check_bundles(rule, context)?,
            "IMP002" => fanout::check(rule, context),
            _ => Vec::new(),
        };
        for mut violation in raised {
            let module = violation.source_module.as_deref().unwrap_or_default();
            if let Some(level) = severity(rule, module, &violation.path) {
                level.clone_into(&mut violation.severity);
                found.push(violation);
            }
        }
    }
    Ok(found)
}

fn skip_import(context: &GraphContext<'_>, detail: &ImportDetail) -> bool {
    detail.type_checking && !context.model.config.import_graph.include_type_checking
}

fn boundary_imports<'a>(context: &GraphContext<'a>) -> Vec<BoundaryImport<'a>> {
    let model = &context.model;
    // A scoped part judges only paths that start and stay inside its packages.
    let mut result: Vec<BoundaryImport<'a>> = context
        .imports
        .all_imports()
        .filter(|detail| {
            model.owns(&detail.importer) && (detail.external || model.owns(&detail.imported))
        })
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
        if !model.owns(module) {
            continue;
        }
        let mut seen: HashSet<(&str, &[String])> = context
            .imports
            .imports_of(module)
            .iter()
            .filter(|detail| !skip_import(context, detail))
            .map(|detail| (detail.imported.as_str(), detail.names.as_slice()))
            .collect();
        for path in context.imports.shortest_chains_from(
            module,
            policy.include_type_checking,
            model.scope_roots(),
        ) {
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
    let path = context.files.sources[&detail.importer].path.clone();
    import_violation_at(rule, context, detail, path, finding)
}

/// An import finding in `path`, which differs from the importer's for a test.
fn import_violation_at(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
    detail: &ImportDetail,
    path: String,
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
        path,
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
    join_or(values, "(nothing)")
}

fn join_or(values: &[String], empty: &str) -> String {
    if values.is_empty() {
        empty.to_owned()
    } else {
        values.join(", ")
    }
}

/// Drop findings that a more specific rule already reports for the same cause.
pub fn deduplicate(violations: Vec<Violation>) -> Vec<Violation> {
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
