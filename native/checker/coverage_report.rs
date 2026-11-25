//! The analysis facts behind a check report's coverage section, as
//! `smelt.engine.coverage` reads them from an analysis context.
use super::analysis::architecture::{ArchitectureModel, ModuleKind};
use super::analysis::coverage::{Coverage, CoverageInputs};
use super::analysis::python_target::PythonTargets;
use super::analysis::test_syntax::TestModule;
use super::config::Request;
use super::config::discovery::{import_root, packages_in};
use super::config::workspace::member_directories;
use super::contracts::Contracts;
use super::files::{FileIndex, SourceFile, relative_path};
use super::patterns::ModulePattern;
use super::rules::definition_order::file_order;
use super::source::ParsedSource;
use super::vfs;
use rayon::prelude::*;
use serde_json::{Map, Value, json};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

const NO_PACKAGE: &str = "no Python package in src/ or the member directory";

pub struct ReportInputs<'a> {
    pub request: &'a Request,
    pub files: &'a FileIndex,
    pub model: &'a ArchitectureModel<'a>,
    pub contracts: &'a Contracts<'a>,
    pub targets: &'a PythonTargets,
    pub sources: &'a [(&'a SourceFile, &'a ParsedSource)],
    /// Whether definition ordering ran, which adds its per-file summary.
    pub ordering: bool,
    /// The analyzed tests, when a test-quality rule ran.
    pub test_modules: Option<&'a [TestModule<'a>]>,
    /// The coverage ARC013 built, when it ran with the same inputs.
    pub coverage: &'a OnceLock<Coverage>,
}

/// The coverage facts the Python report completes with its configuration.
pub fn facts(inputs: &ReportInputs<'_>) -> Result<Value, String> {
    let coverage = Coverage::shared(
        inputs.coverage,
        inputs.files,
        inputs.model,
        &CoverageInputs {
            project: &inputs.request.project,
            sources: inputs.sources,
            strict: inputs.request.strict_coverage,
        },
    )?;
    let mut facts = match coverage.summary() {
        Value::Object(summary) => summary,
        _ => Map::new(),
    };
    facts.insert(
        "inventory".to_owned(),
        coverage
            .entries
            .iter()
            .map(|entry| entry.to_json())
            .collect(),
    );
    facts.insert("modules".to_owned(), classification(inputs));
    facts.insert("contracts".to_owned(), contracts(inputs)?);
    facts.insert(
        "python_targets".to_owned(),
        Value::Array(
            inputs.targets.describe(
                inputs
                    .files
                    .source_order
                    .iter()
                    .filter_map(|module| inputs.files.sources.get(module))
                    .map(|source| source.absolute.as_path()),
            ),
        ),
    );
    facts.insert(
        "omitted_workspace_members".to_owned(),
        Value::Array(omitted_members(inputs)?),
    );
    if inputs.ordering && !inputs.sources.is_empty() {
        let mut summaries: Vec<(&str, Value)> = inputs
            .sources
            .par_iter()
            .map(|(source, parsed)| {
                let order = file_order(source, parsed, inputs.contracts, inputs.targets);
                (source.path.as_str(), order.summary(&source.path))
            })
            .collect();
        summaries.sort_by(|left, right| left.0.cmp(right.0));
        facts.insert(
            "ordering".to_owned(),
            summaries.into_iter().map(|(_, summary)| summary).collect(),
        );
    }
    if let Some(modules) = inputs.test_modules.filter(|modules| !modules.is_empty()) {
        facts.insert(
            "test_quality".to_owned(),
            json!({
                "files": modules.len(),
                "collected_tests": modules.iter().map(|module| module.tests.len()).sum::<usize>(),
            }),
        );
    }
    Ok(Value::Object(facts))
}

/// Source modules by architecture kind, with wiring and central ones counted apart.
fn classification(inputs: &ReportInputs<'_>) -> Value {
    let kinds = [
        ModuleKind::Feature,
        ModuleKind::Shared,
        ModuleKind::CompositionRoot,
        ModuleKind::Unclassified,
    ];
    let mut counts = [0usize; 4];
    let (mut wiring, mut central) = (0, 0);
    for module in inputs.files.sources.keys() {
        let info = inputs.model.info(module);
        let kind = info
            .as_deref()
            .map_or(ModuleKind::Unclassified, |info| info.kind);
        if let Some(index) = kinds.iter().position(|item| *item == kind) {
            counts[index] += 1;
        }
        wiring += usize::from(info.as_deref().is_some_and(|info| info.wiring));
        central += usize::from(info.as_deref().is_some_and(|info| info.central));
    }
    let mut report = Map::new();
    report.insert("total".to_owned(), json!(inputs.files.sources.len()));
    for (kind, count) in kinds.iter().zip(counts) {
        report.insert(kind.value().to_owned(), json!(count));
    }
    report.insert("wiring".to_owned(), json!(wiring));
    report.insert("central".to_owned(), json!(central));
    Value::Object(report)
}

/// Each resolved contract with the required and public modules it lacks.
fn contracts(inputs: &ReportInputs<'_>) -> Result<Value, String> {
    let known: HashSet<&str> = inputs
        .files
        .sources
        .keys()
        .chain(&inputs.files.packages)
        .map(String::as_str)
        .collect();
    let mut described = Vec::new();
    for contract in &inputs.contracts.resolved {
        let missing: Vec<String> = contract
            .spec
            .required
            .iter()
            .map(|entry| contract.qualify(entry))
            .filter(|module| !known.contains(module.as_str()))
            .collect();
        let mut missing_public = Vec::new();
        for pattern in contract.public().unwrap_or_default() {
            let compiled = ModulePattern::new(&pattern)?;
            if !known.iter().any(|module| compiled.matches(module)) {
                missing_public.push(pattern);
            }
        }
        described.push(json!({
            "name": contract.name,
            "package": contract.package,
            "missing": missing,
            "missing_public": missing_public,
        }));
    }
    Ok(Value::Array(described))
}

/// Workspace members, declared next to the config, whose code is not analyzed.
fn omitted_members(inputs: &ReportInputs<'_>) -> Result<Vec<Value>, String> {
    let root = &inputs.files.root;
    let configured: HashSet<&str> = inputs
        .request
        .project
        .root_packages
        .iter()
        .map(String::as_str)
        .collect();
    // Resolving every source costs a lookup each, so only a member's packages pay for it.
    let analyzed = std::cell::OnceCell::new();
    let analyzed = || {
        analyzed.get_or_init(|| {
            inputs
                .files
                .sources
                .values()
                .flat_map(|source| {
                    real(&source.absolute)
                        .ancestors()
                        .skip(1)
                        .map(Path::to_path_buf)
                        .collect::<Vec<_>>()
                })
                .collect::<HashSet<PathBuf>>()
        })
    };
    let mut omitted = Vec::new();
    for member in member_directories(root, false)? {
        if let Some(reason) = member.skipped {
            omitted.push(json!({"path": member.path, "reason": reason}));
            continue;
        }
        let Some(base) = import_root(&root.join(&member.path)) else {
            omitted.push(json!({"path": member.path, "reason": NO_PACKAGE}));
            continue;
        };
        let source_root = relative_path(root, &base);
        let packages: Vec<String> = packages_in(&base)
            .iter()
            .filter_map(|package| package.file_name())
            .map(|name| name.to_string_lossy().into_owned())
            .collect();
        let missing: Vec<&str> = packages
            .iter()
            .map(String::as_str)
            .filter(|name| !configured.contains(name))
            .collect();
        let uncovered: Vec<String> = packages
            .iter()
            .filter(|name| configured.contains(name.as_str()))
            .filter(|name| !analyzed().contains(&real(&root.join(&source_root).join(name))))
            .map(|name| format!("{source_root}/{name}"))
            .collect();
        let mut reasons = Vec::new();
        if !missing.is_empty() {
            reasons.push(format!(
                "not in project.root_packages: {}",
                missing.join(", ")
            ));
        }
        if !uncovered.is_empty() {
            reasons.push(format!(
                "no analyzed source files in: {}",
                uncovered.join(", ")
            ));
        }
        if !reasons.is_empty() {
            omitted.push(json!({"path": member.path, "reason": reasons.join("; ")}));
        }
    }
    Ok(omitted)
}

fn real(path: &Path) -> PathBuf {
    vfs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}
