//! MET005: package facades too empty for an enabled IMP004 to check anything.
use super::analysis::imports::type_checking_lines;
use super::analysis::installed::Exports;
use super::analysis::namespace::resolve_relative;
use super::analysis::nodes::walk_statements;
use super::contracts::Contracts;
use super::files::{FileIndex, SourceFile};
use super::patterns::is_within;
use super::rules::structure::package_imports::{covering_package, package_facades};
use super::settings::{CatalogRule, Settings};
use super::source::ParsedSource;
use super::violation::Violation;
use ruff_python_ast::Stmt;
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};

const PREFER_PACKAGE_IMPORT: &str = "IMP004";
const EXPORT_GAP: &str = "ARC008";
const SAMPLE: usize = 5;

/// What MET005 reads from the run.
pub struct FacadeInputs<'a> {
    pub catalog: &'a [CatalogRule],
    pub settings: &'a Settings<'a>,
    pub contracts: &'a Contracts<'a>,
    pub files: &'a FileIndex,
    pub sources: &'a [(&'a SourceFile, &'a ParsedSource)],
    pub test_roots: &'a [String],
    pub include_type_checking: bool,
    /// The most outside callers a facade may leave uncovered.
    pub limit: usize,
}

/// IMP004-coded evidence: outside imports no eligible facade can supply.
///
/// The caller filters them through ignore entries like real IMP004 findings
/// before [`uncovered_facades`] counts them.
pub fn uncovered_imports(inputs: &FacadeInputs<'_>) -> Vec<Violation> {
    let Some(rule) = inputs
        .catalog
        .iter()
        .find(|rule| rule.code == PREFER_PACKAGE_IMPORT)
    else {
        return Vec::new();
    };
    if !inputs.settings.has_explicit_activation(&rule.code) {
        return Vec::new();
    }
    let facades = package_facades(inputs.sources);
    let mut found = Vec::new();
    for (source, parsed) in inputs.sources {
        if source.is_package
            || inputs.files.is_test_path(&source.path, inputs.test_roots)
            || inputs
                .settings
                .explicit_activation(&rule.code, inputs.contracts, &source.module, &source.path)
                .is_none()
        {
            continue;
        }
        let guarded = type_checking_lines(parsed);
        for statement in walk_statements(parsed.module()) {
            let Stmt::ImportFrom(node) = statement else {
                continue;
            };
            if node
                .names
                .iter()
                .any(|alias| alias.name.starts_with('_') || alias.name.as_str() == "*")
            {
                continue;
            }
            let (line, column) = parsed.text.position(usize::from(node.start()));
            if !inputs.include_type_checking && guarded.contains(&line) {
                continue;
            }
            let written = node.module.as_ref().map(|name| name.as_str());
            let base = resolve_relative(&source.module, false, node.level, written);
            if inputs
                .files
                .sources
                .get(&base)
                .is_none_or(|target| target.is_package)
            {
                continue;
            }
            let names: Vec<&str> = node.names.iter().map(|alias| alias.name.as_str()).collect();
            if covering_package(&facades, &base, &source.module, &names, false).is_some() {
                continue; // IMP004 itself reports this statement.
            }
            let Some(package) = enclosing_facade(&facades, &base, &source.module) else {
                continue;
            };
            let exports = &facades[package];
            let unresolved: Vec<&str> = names
                .iter()
                .copied()
                .filter(|name| exports.get(*name) != Some(&format!("{base}.{name}")))
                .collect();
            found.push(Violation {
                code: rule.code.clone(),
                rule: rule.name.clone(),
                category: rule.category.clone(),
                severity: rule.default_severity.clone(),
                message: format!("{package} cannot supply this import"),
                path: source.path.clone(),
                line: Some(line),
                column: Some(column + 1),
                end_line: Some(line),
                end_column: None,
                source_module: Some(source.module.clone()),
                target_module: Some(base.clone()),
                import_chain: Vec::new(),
                feature: None,
                layer: None,
                edge: None,
                expected: json!({"package": package, "unresolved": unresolved}),
                hint: None,
                fix: None,
            });
        }
    }
    found
}

#[derive(Default)]
struct Evidence<'a> {
    callers: BTreeSet<&'a str>,
    modules: BTreeSet<&'a str>,
    names: BTreeSet<String>,
}

/// One hint per facade whose uncovered outside callers exceed the limit.
pub fn uncovered_facades(
    inputs: &FacadeInputs<'_>,
    evidence: &[Violation],
    diagnostic: &CatalogRule,
    severity: &str,
) -> Vec<Violation> {
    let mut packages: BTreeMap<&str, Evidence<'_>> = BTreeMap::new();
    for item in evidence {
        let (Some(package), Some(caller)) = (
            item.expected["package"].as_str(),
            item.source_module.as_deref(),
        ) else {
            continue;
        };
        let target = item.target_module.as_deref().unwrap_or_default();
        let found = packages.entry(package).or_default();
        found.callers.insert(caller);
        found.modules.insert(target);
        found.names.extend(
            item.expected["unresolved"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(|name| format!("{target}.{name}")),
        );
    }
    let mut found = Vec::new();
    for (package, evidence) in packages {
        let Some(initializer) = inputs.files.sources.get(package) else {
            continue;
        };
        if evidence.callers.len() <= inputs.limit
            || governed_by_export_gap(inputs, package, &initializer.path)
        {
            continue;
        }
        let callers: Vec<&str> = evidence.callers.into_iter().collect();
        let names: Vec<String> = evidence.names.into_iter().collect();
        found.push(Violation {
            code: diagnostic.code.clone(),
            rule: diagnostic.name.clone(),
            category: diagnostic.category.clone(),
            severity: severity.to_owned(),
            message: format!(
                "{package} does not re-export {} names that {} outside modules import from \
                 {} implementation modules, so imports/prefer-package-import cannot apply",
                names.len(),
                callers.len(),
                evidence.modules.len()
            ),
            path: initializer.path.clone(),
            line: Some(1),
            column: Some(1),
            end_line: Some(1),
            end_column: None,
            source_module: Some(package.to_owned()),
            target_module: None,
            import_chain: Vec::new(),
            feature: None,
            layer: None,
            edge: Some(format!("{}:{package}", diagnostic.code)),
            expected: json!({
                "package": package,
                "callers": callers.len(),
                "implementation_modules": evidence.modules.len(),
                "names": names.len(),
                "sample_callers": &callers[..callers.len().min(SAMPLE)],
                "sample_names": &names[..names.len().min(SAMPLE)],
                "max": inputs.limit,
            }),
            hint: Some(format!(
                "Review whether to re-export these names. Re-exports can change import-time \
                 behavior. For an intentionally facade-less package, add a reasoned MET005 \
                 ignore entry for {package}."
            )),
            fix: None,
        });
    }
    found
}

/// The nearest package with an initializer around `base` that excludes `caller`.
fn enclosing_facade<'f>(
    facades: &'f HashMap<String, Exports>,
    base: &str,
    caller: &str,
) -> Option<&'f str> {
    let mut package = base.rsplit_once('.').map_or("", |(parent, _)| parent);
    while !package.is_empty() && !is_within(caller, package) {
        if let Some((name, _)) = facades.get_key_value(package) {
            return Some(name.as_str());
        }
        package = package.rsplit_once('.').map_or("", |(parent, _)| parent);
    }
    None
}

/// Whether an active ARC008 export threshold already reviews this package.
fn governed_by_export_gap(inputs: &FacadeInputs<'_>, package: &str, path: &str) -> bool {
    let Some(rule) = inputs.catalog.iter().find(|rule| rule.code == EXPORT_GAP) else {
        return false;
    };
    inputs.contracts.resolved.iter().any(|contract| {
        contract.spec.min_importers_to_export.is_some() && contract.package == package
    }) && inputs
        .settings
        .severity(rule, inputs.contracts, package, path, false)
        .is_some()
}
