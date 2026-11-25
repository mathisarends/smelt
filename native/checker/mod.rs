//! The Rust analysis core. `--internal-check` serves the check engine;
//! `scripts/compare_rust_core.py` compares its findings with the Python engine's.
mod analysis;
mod config;
mod contracts;
mod coverage_report;
mod facade_coverage;
mod files;
mod meta;
mod patterns;
mod profile;
mod renames;
mod rule_policy;
mod rules;
mod settings;
mod snapshot;
mod source;
mod suppressions;
mod symbols;
mod vfs;
mod violation;

use analysis::architecture::{ArchitectureModel, Scope};
use analysis::facade_usage::FacadeUsage;
use analysis::imports::ImportIndex;
use analysis::python_target::PythonTargets;
use analysis::test_syntax::{self, TestModule};
use config::Request;
use config::errors::ConfigError;
use contracts::{Contracts, FacadePolicy};
use coverage_report::ReportInputs;
use facade_coverage::{FacadeInputs, uncovered_facades, uncovered_imports};
use files::{FileIndex, SourceFile};
use meta::{META_RULES, SuppressionAudit};
use patterns::{ModulePattern, PathPattern};
use rayon::prelude::*;
use rule_policy::{PolicyInputs, missing_rule_policies};
use rules::TEST_SOURCE_RULES;
use rules::dependencies::{self, GraphContext, IMPORT_RULES, TEST_IMPORT_RULES};
use rules::structure::{self, STRUCTURE_RULES, StructureContext, TEST_READING_RULES};
use rules::testing::{self, TEST_RULES, TestProject};
use serde::Deserialize;
use serde_json::{Value, json};
use settings::{CatalogRule, Settings};
use snapshot::Snapshot;
use source::ParsedSource;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use suppressions::Suppression;
use violation::Violation;

const CATALOG: &str = include_str!("../../smelt/rules/catalog.json");
const ANALYSIS_STACK_BYTES: usize = 256 * 1024 * 1024;
/// The strict-coverage guard, which ignores and suppressions cannot hide.
/// Meta findings about the configuration rather than a file's findings.
const CONFIGURATION_FINDINGS: [&str; 2] = ["MET004", "MET005"];
const STRICT_COVERAGE: &str = "ARC013";
/// The rules each architecture of a uv workspace runs for its own files.
const ARCHITECTURE_SCOPED: [&str; 8] = [
    "ARC001", "ARC002", "ARC003", "ARC004", "ARC005", "ARC006", "ARC011", "ARC012",
];
/// Rules whose findings add test-quality coverage to the report.
const TEST_QUALITY_RULES: [&str; 9] = [
    "TST002", "TST003", "TST004", "TST005", "TST006", "TST007", "TST008", "TST009", "TST010",
];
/// The rule whose per-file summaries the report's ordering coverage lists.
const DEFINITION_ORDER: &str = "LAY004";
/// The analysis indexes built without parsing a source.
const SYNTAX_FREE: [&str; 3] = ["files", "model", "workspace"];

/// One check run, as the Python engine requests it.
#[derive(Deserialize)]
pub struct Options {
    config: PathBuf,
    /// Rule codes the run is narrowed to; empty runs the configured rules.
    #[serde(default)]
    select: Vec<String>,
    /// Rule codes the run leaves out.
    #[serde(default)]
    ignore: Vec<String>,
    /// Enable architecture/uncovered-source for this run.
    #[serde(default)]
    strict_coverage: bool,
    /// Judge the configuration itself (MET004, MET005); a scoped run does not.
    #[serde(default)]
    configuration_findings: bool,
    /// Check the staged Git index, whose sources and configuration replace the working tree's.
    #[serde(default)]
    index: Option<StagedIndex>,
    /// Also check this revision with the run's configuration, for the findings it already had.
    #[serde(default)]
    baseline: Option<Baseline>,
    /// The requesting interpreter's import search path, for installed packages.
    #[serde(default)]
    search_path: Option<Vec<PathBuf>>,
    /// Run the rules; without them the outcome only describes the files and coverage.
    #[serde(default = "run_rules")]
    rules: bool,
}

fn run_rules() -> bool {
    true
}

#[derive(Deserialize)]
struct StagedIndex {
    repository: PathBuf,
    /// The blob ID of each staged file, by repository-relative path.
    blobs: BTreeMap<String, String>,
}

#[derive(Deserialize)]
struct Baseline {
    repository: PathBuf,
    /// The commit to compare with.
    revision: String,
}

/// The findings of one run and what the report says about its coverage.
struct Outcome {
    violations: Vec<Value>,
    suppressed: usize,
    coverage: Value,
    modules: usize,
    paths: Vec<String>,
    /// The SHA-256 of each text a fixable finding was found in, by path.
    digests: BTreeMap<String, String>,
    /// The baseline revision's findings, each with the text of its line.
    baseline: Option<Vec<Value>>,
    /// The sources, packages and tests the check discovered.
    files: Value,
    /// Each `architecture.canonical_exports` declaration that failed, with why.
    ownership_issues: Vec<(String, String)>,
}

/// Run the check that stdin requests as JSON `Options` and print its outcome.
///
/// An error prints `{"error": message}` and exits 2, so the engine can raise it.
pub fn serve_check() -> i32 {
    profile::start();
    let code = respond_to_check();
    profile::report();
    code
}

fn respond_to_check() -> i32 {
    let mut input = String::new();
    let outcome = profile::time("check request", || {
        io::stdin()
            .read_to_string(&mut input)
            .map_err(|error| error.to_string())
            .and_then(|_| {
                serde_json::from_str::<Options>(&input).map_err(|error| error.to_string())
            })
            .map_err(Failure::Message)
            .and_then(run)
    });
    let _span = profile::span("write outcome");
    let (output, code) = match outcome {
        Ok(outcome) => (
            json!({
                "violations": outcome.violations,
                "suppressed": outcome.suppressed,
                "coverage": outcome.coverage,
                "modules": outcome.modules,
                "paths": outcome.paths,
                "digests": outcome.digests,
                "baseline": outcome.baseline,
                "files": outcome.files,
                "ownership_issues": outcome.ownership_issues,
            }),
            0,
        ),
        Err(Failure::Message(message)) => (json!({"error": message}), 2),
        Err(Failure::Config(error)) => (
            json!({
                "error": error.render(),
                "config": {
                    "source": error.source,
                    "issues": error.issues.iter().map(|issue| json!({
                        "path": issue.path,
                        "message": issue.message,
                        "source": issue.source,
                    })).collect::<Vec<_>>(),
                },
            }),
            2,
        ),
    };
    match write_output(&output.to_string()) {
        Ok(()) => code,
        Err(message) => {
            eprintln!("smelt core: {message}");
            2
        }
    }
}

/// The project settings discovery reads, as the engine sends them.
#[derive(Deserialize)]
struct FilesRequest {
    root: PathBuf,
    root_packages: Vec<String>,
    source_roots: Vec<String>,
    test_roots: Vec<String>,
    exclude: Vec<String>,
    /// Whether a file naming policy reads YAML files.
    yaml: bool,
    /// Also report the imports between uv workspace members, as `member_edges`.
    #[serde(default)]
    member_edges: bool,
}

/// Discover the files of the project stdin describes and print the inventory.
///
/// An error prints `{"error": message}` and exits 2, as a check does.
pub fn serve_files() -> i32 {
    let mut input = String::new();
    let inventory = io::stdin()
        .read_to_string(&mut input)
        .map_err(|error| error.to_string())
        .and_then(|_| {
            serde_json::from_str::<FilesRequest>(&input).map_err(|error| error.to_string())
        })
        .and_then(|request| {
            let project = config::Project {
                root_packages: request.root_packages,
                source_roots: request.source_roots,
                test_roots: request.test_roots,
                exclude: request.exclude,
                exclusions: Vec::new(),
                python_version: None,
                uv_workspace: false,
            };
            let files = FileIndex::scan(&request.root, &project)?;
            let mut inventory = files.inventory(request.yaml);
            if request.member_edges {
                inventory["member_edges"] = json!(member_edges(&files, &project.root_packages)?);
            }
            Ok(inventory)
        });
    let (output, code) = match inventory {
        Ok(inventory) => (inventory, 0),
        Err(message) => (json!({"error": message}), 2),
    };
    match write_output(&output.to_string()) {
        Ok(()) => code,
        Err(message) => {
            eprintln!("smelt core: {message}");
            2
        }
    }
}

/// The imports between the uv workspace members of `files`.
fn member_edges(files: &FileIndex, roots: &[String]) -> Result<Vec<(String, String)>, String> {
    let sources: Vec<&SourceFile> = files.sources.values().collect();
    with_analysis_pool(|| {
        let parsed = parse_sources(&sources, true)?;
        let pairs: Vec<(&SourceFile, &ParsedSource)> =
            sources.iter().copied().zip(&parsed).collect();
        let imports = ImportIndex::build(files, roots, &pairs, &[])?;
        analysis::workspace::member_edges(files, &imports)
    })?
}

/// Print the symbols of the modules stdin lists, for `smelt init` to trace.
///
/// An error prints `{"error": message}` and exits 2, as a check does.
pub fn serve_symbols() -> i32 {
    let mut input = String::new();
    let report = io::stdin()
        .read_to_string(&mut input)
        .map_err(|error| error.to_string())
        .and_then(|_| {
            serde_json::from_str::<symbols::SymbolsRequest>(&input)
                .map_err(|error| error.to_string())
        })
        .and_then(|request| with_analysis_pool(|| symbols::symbols(&request)));
    let (output, code) = match report {
        Ok(found) => (json!({"symbols": found}), 0),
        Err(message) => (json!({"error": message}), 2),
    };
    match write_output(&output.to_string()) {
        Ok(()) => code,
        Err(message) => {
            eprintln!("smelt core: {message}");
            2
        }
    }
}

/// Load one configuration and print it, or its error, as JSON.
pub fn serve_config(path: &Path) -> i32 {
    let description = match config::load::load_config(path) {
        Ok(loaded) => config::load::describe(&loaded),
        Err(error) => json!({"ok": false, "error": error.render()}),
    };
    match write_output(&description.to_string()) {
        Ok(()) => 0,
        Err(message) => {
            eprintln!("smelt core: {message}");
            2
        }
    }
}

fn write_output(output: &str) -> Result<(), String> {
    let mut stdout = io::stdout().lock();
    stdout
        .write_all(output.as_bytes())
        .and_then(|()| stdout.flush())
        .map_err(|error| error.to_string())
}

/// Why a check failed.
enum Failure {
    /// A configuration the user must fix, reported with its issues.
    Config(ConfigError),
    Message(String),
}

impl From<String> for Failure {
    fn from(message: String) -> Self {
        Self::Message(message)
    }
}

fn run(options: Options) -> Result<Outcome, Failure> {
    if let Some(entries) = &options.search_path {
        analysis::installed::use_search_path(entries.clone());
    }
    let index = profile::time("mount staged index", || {
        options
            .index
            .as_ref()
            .map(|staged| Snapshot::index(&staged.repository, &staged.blobs))
            .transpose()
    })?;
    let config = match (&index, &options.index) {
        (Some(snapshot), Some(staged)) => snapshot.locate(&staged.repository, &options.config)?,
        _ => options.config.clone(),
    };
    let load = || {
        let _span = profile::span("load configuration");
        let mut request = Request::load(&config, options.select.clone())?;
        if options.strict_coverage {
            request.force_strict_coverage()?;
        }
        Ok::<_, String>(request)
    };
    let request = load()?;
    let top = index.as_ref().map(Snapshot::tree).or(options
        .baseline
        .as_ref()
        .map(|baseline| baseline.repository.as_path()));
    // The base revision is written and checked while the run's own check proceeds.
    std::thread::scope(|scope| {
        let before = options
            .baseline
            .as_ref()
            .zip(top)
            .map(|(baseline, top)| {
                std::thread::Builder::new()
                    // The analysis pool and the profile name their threads after it.
                    .name("baseline".to_owned())
                    .stack_size(ANALYSIS_STACK_BYTES)
                    .spawn_scoped(scope, || {
                        baseline_findings(baseline, top, load()?, &options.ignore)
                    })
                    .map_err(|error| Failure::from(error.to_string()))
            })
            .transpose()?;
        let mut outcome = check(
            &request,
            &options.ignore,
            options.configuration_findings,
            options.rules,
        )?;
        if let Some(before) = before {
            let findings = before
                .join()
                .map_err(|_| Failure::from("the baseline check panicked".to_owned()))??;
            outcome.baseline = Some(findings);
        }
        Ok(outcome)
    })
}

/// The findings `baseline` already had, judged by `request`, with their lines.
///
/// `top` is the repository's top level as `request` sees it.
fn baseline_findings(
    baseline: &Baseline,
    top: &Path,
    mut request: Request,
    ignore: &[String],
) -> Result<Vec<Value>, Failure> {
    let snapshot = profile::time("mount base revision", || {
        Snapshot::revision(&baseline.repository, &baseline.revision)
    })?;
    let root = snapshot.locate(top, &request.root)?;
    // A project the revision lacks, or one without files, had no findings.
    if !vfs::is_dir(&root) {
        return Ok(Vec::new());
    }
    request.root = root;
    // Like the Python engine, judge a revision only when it has files to check.
    if !has_check_paths(&request)? {
        return Ok(Vec::new());
    }
    let before = check(&request, ignore, false, true)?;
    Ok(with_lines(&request.root, before.violations))
}

/// Whether the project has a source, test or named YAML file to check.
fn has_check_paths(request: &Request) -> Result<bool, String> {
    let files = FileIndex::discover(request)?;
    Ok(!files.sources.is_empty()
        || !files.tests.is_empty()
        || (request.file_naming.checks_yaml() && !files.yaml_paths().is_empty()))
}

/// Each finding with the text of its line, as the Python engine fingerprints it.
fn with_lines(root: &Path, violations: Vec<Value>) -> Vec<Value> {
    let mut texts: HashMap<String, Vec<String>> = HashMap::new();
    violations
        .into_iter()
        .map(|mut violation| {
            let path = violation["path"].as_str().unwrap_or_default().to_owned();
            let line = violation["line"].as_u64().unwrap_or(0) as usize;
            let text = if path.is_empty() || line == 0 {
                String::new()
            } else {
                let lines = texts
                    .entry(path.clone())
                    .or_insert_with(|| python_lines(&root.join(&path)));
                lines.get(line - 1).cloned().unwrap_or_default()
            };
            violation["snippet"] = json!(text);
            violation
        })
        .collect()
}

/// The lines of a text file as Python's `str.splitlines` splits them.
fn python_lines(path: &Path) -> Vec<String> {
    let Ok(text) = config::paths::read_text(path) else {
        return Vec::new();
    };
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let mut lines: Vec<String> = text
        .split([
            '\n', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}', '\u{2029}',
        ])
        .map(str::to_owned)
        .collect();
    if lines.last().is_some_and(String::is_empty) {
        lines.pop();
    }
    lines
}

/// Check the project `request` describes, leaving out the `ignore` rules, or
/// without rules only describe its files and coverage.
///
/// The whole check runs on an analysis pool, so discovery lists directories in
/// parallel too, and every walk of the check shares their listings.
fn check(
    request: &Request,
    ignore: &[String],
    configuration_findings: bool,
    with_rules: bool,
) -> Result<Outcome, Failure> {
    let _listings = vfs::ListingCache::enable();
    with_analysis_pool(|| check_in_pool(request, ignore, configuration_findings, with_rules))?
}

fn check_in_pool(
    request: &Request,
    ignore: &[String],
    configuration_findings: bool,
    with_rules: bool,
) -> Result<Outcome, Failure> {
    let catalog: Vec<CatalogRule> =
        serde_json::from_str(CATALOG).map_err(|error| error.to_string())?;
    let files = profile::time("discover files", || FileIndex::discover(request))?;
    let contracts = profile::time("resolve contracts", || Contracts::resolve(request, &files))?;
    let settings = Settings::new(&catalog, request);
    let ignored = |rule: &&CatalogRule| !with_rules || ignore.contains(&rule.code);
    // The meta rules judge the run after filtering instead of checking sources.
    let (meta_rules, active): (Vec<&CatalogRule>, Vec<&CatalogRule>) = settings
        .active(&request.select)
        .into_iter()
        .filter(|rule| !ignored(rule))
        .partition(|rule| META_RULES.contains(&rule.code.as_str()));
    // The rules a full run with this configuration executes, as MET004 and MET005 judge it.
    let configured: HashSet<&str> = settings
        .active(&[])
        .into_iter()
        .filter(|rule| !ignored(rule))
        .map(|rule| rule.code.as_str())
        .collect();
    // Conflicting facade thresholds are a configuration error, not a failed rule.
    for (code, policy) in [
        ("ARC008", FacadePolicy::Export),
        ("IMP001", FacadePolicy::Bundle),
    ] {
        if active.iter().any(|rule| rule.code == code) {
            contracts.facade_policies(policy).map_err(Failure::Config)?;
        }
    }
    let report_ordering = active.iter().any(|rule| rule.code == DEFINITION_ORDER);
    let report_test_quality = active
        .iter()
        .any(|rule| TEST_QUALITY_RULES.contains(&rule.code.as_str()));
    let ignores = Ignores::new(request, &settings)?;
    let names: HashMap<&str, &str> = catalog
        .iter()
        .map(|rule| (rule.name.as_str(), rule.code.as_str()))
        .collect();
    let meta_rule = |code: &str| {
        meta_rules
            .iter()
            .copied()
            .find(|rule| rule.code == code)
            .map(|rule| (rule, settings.active_severity(rule)))
    };
    let audit = SuppressionAudit {
        unused: meta_rule("MET001"),
        reason: meta_rule("MET002").filter(|_| request.require_reason),
        known: catalog.iter().map(|rule| rule.code.as_str()).collect(),
        // The rules a full run would execute; the others are off and need no suppression.
        enabled: settings
            .active(&[])
            .into_iter()
            .map(|rule| rule.code.as_str())
            .filter(|code| !META_RULES.contains(code))
            .collect(),
        checked: active.iter().map(|rule| rule.code.as_str()).collect(),
        names: &names,
    };
    let targets = PythonTargets::new(
        &files.root,
        &request.project.source_roots,
        request.project.python_version.as_deref(),
    );
    let (test_rules, other_rules): (Vec<&CatalogRule>, Vec<&CatalogRule>) = active
        .iter()
        .copied()
        .partition(|rule| TEST_RULES.contains(&rule.code.as_str()));
    let (import_rules, other_rules): (Vec<&CatalogRule>, Vec<&CatalogRule>) = other_rules
        .into_iter()
        .partition(|rule| IMPORT_RULES.contains(&rule.code.as_str()));
    let (structure_rules, file_rules): (Vec<&CatalogRule>, Vec<&CatalogRule>) = other_rules
        .into_iter()
        .partition(|rule| STRUCTURE_RULES.contains(&rule.code.as_str()));
    let analysis = Analysis {
        active: &file_rules,
        project: rules::Project {
            contracts: &contracts,
            targets: &targets,
            roots: &request.project.root_packages,
        },
        settings: &settings,
        selected: !request.select.is_empty(),
    };
    let sources: Vec<&SourceFile> = files.sources.values().collect();
    let quality_exclude = path_patterns(&request.tests.quality_exclude)?;
    let forbidden_mocks = path_patterns(&request.tests.forbidden_mock_paths)?;
    let all_tests: Vec<&SourceFile> = files.tests.values().collect();
    let known: HashSet<&str> = files
        .sources
        .keys()
        .chain(&files.packages)
        .map(String::as_str)
        .collect();
    let test_project = TestProject {
        policy: &request.tests,
        known: &known,
        forbidden_mocks: &forbidden_mocks,
    };
    // A source that does not parse stops the check only when a rule reads syntax.
    let needs_syntax = active.iter().any(|rule| {
        rule.requires
            .iter()
            .any(|index| !SYNTAX_FREE.contains(&index.as_str()))
    }) || (configuration_findings
        && meta_rule("MET005").is_some()
        && configured.contains("IMP004"));
    let mut ownership_issues = Vec::new();
    let (mut violations, suppressed, coverage, digests) = (|| {
        let parsed = profile::time("parse sources", || parse_sources(&sources, needs_syntax))?;
        let source_pairs: Vec<(&SourceFile, &ParsedSource)> =
            sources.iter().copied().zip(&parsed).collect();
        // Ownership declarations are verified against the static exports of the sources.
        if !request.canonical_exports.is_empty() {
            let usage = FacadeUsage::new(
                &files,
                &source_pairs,
                &[],
                &request.project.test_roots,
                HashMap::new(),
            );
            ownership_issues = usage.canonical_exports(&request.canonical_exports).1;
        }
        let imports_tests = import_rules
            .iter()
            .any(|rule| TEST_IMPORT_RULES.contains(&rule.code.as_str()));
        let test_source_rules: Vec<&CatalogRule> = file_rules
            .iter()
            .copied()
            .filter(|rule| TEST_SOURCE_RULES.contains(&rule.code.as_str()))
            .collect();
        let reads_tests = !test_rules.is_empty()
            || !test_source_rules.is_empty()
            || imports_tests
            || structure_rules
                .iter()
                .any(|rule| TEST_READING_RULES.contains(&rule.code.as_str()));
        let parsed_tests = if reads_tests {
            profile::time("parse tests", || parse_all(&all_tests))?
        } else {
            Vec::new()
        };
        let all_test_pairs: Vec<(&SourceFile, &ParsedSource)> =
            all_tests.iter().copied().zip(&parsed_tests).collect();
        let mut outcomes: Vec<Vec<Violation>> =
            profile::time("file rules", || {
                let mut outcomes: Vec<Vec<Violation>> = source_pairs
                    .par_iter()
                    .map(|(source, parsed)| analysis.file(source, parsed))
                    .collect();
                if !test_source_rules.is_empty() {
                    outcomes.par_extend(all_test_pairs.par_iter().map(|(test, parsed)| {
                        analysis.test_source(&test_source_rules, test, parsed)
                    }));
                }
                outcomes
            });
        let shared_coverage = OnceLock::new();
        let roots = &request.project.root_packages;
        let model_span = profile::span("architecture model");
        let model = if request.workspace_packages.is_empty() {
            ArchitectureModel::build(&request.architecture, roots, &files, Scope::Project)
        } else {
            let root_packages = request
                .workspace_packages
                .get("")
                .map_or(&[][..], Vec::as_slice);
            // Python's order: the root part first, then the members by path.
            let parts: Vec<ArchitectureModel<'_>> = request
                .workspace_packages
                .iter()
                .filter_map(|(scope, packages)| {
                    let architecture = if scope.is_empty() {
                        Some(&request.architecture)
                    } else {
                        request.member_architectures.get(scope)
                    }?;
                    Some(ArchitectureModel::build(
                        architecture,
                        roots,
                        &files,
                        Scope::Part(packages),
                    ))
                })
                .collect();
            ArchitectureModel::combine(&request.architecture, roots, &files, root_packages, parts)
        };
        drop(model_span);
        if !import_rules.is_empty() || !structure_rules.is_empty() {
            let imported_tests: &[(&SourceFile, &ParsedSource)] =
                if imports_tests { &all_test_pairs } else { &[] };
            let imports = if import_rules.is_empty() {
                None
            } else {
                Some(profile::time("import index", || {
                    ImportIndex::build(
                        &files,
                        &request.project.root_packages,
                        &source_pairs,
                        imported_tests,
                    )
                })?)
            };
            let graph = |import_rules: &[&CatalogRule],
                         structure_rules: &[&CatalogRule],
                         model: &ArchitectureModel<'_>|
             -> Result<Vec<Violation>, String> {
                let severity = |rule, module: &str, path: &str| {
                    settings.severity(rule, &contracts, module, path, analysis.selected)
                };
                let mut found = Vec::new();
                if let Some(imports) = imports.as_ref().filter(|_| !import_rules.is_empty()) {
                    let context = GraphContext {
                        imports,
                        model,
                        files: &files,
                        contracts: &contracts,
                        sources: &source_pairs,
                        tests: &all_test_pairs,
                        test_roots: &request.project.test_roots,
                        test_policy: &request.tests,
                        root_packages: &request.project.root_packages,
                        canonical_exports: &request.canonical_exports,
                        namespaces: OnceLock::new(),
                        facade_usage: OnceLock::new(),
                        uv_workspace: request.project.uv_workspace,
                        workspace: OnceLock::new(),
                    };
                    found.extend(dependencies::findings(import_rules, &context, severity)?);
                }
                if !structure_rules.is_empty() {
                    let context = StructureContext {
                        files: &files,
                        model,
                        contracts: &contracts,
                        sources: &source_pairs,
                        tests: &all_test_pairs,
                        test_roots: &request.project.test_roots,
                        forbidden_names: &request.forbidden_names,
                        file_naming: &request.file_naming,
                        project: &request.project,
                        test_policy: &request.tests,
                        strict_coverage: request.strict_coverage,
                        coverage: &shared_coverage,
                        quality_exclude: &quality_exclude,
                        member_usage: OnceLock::new(),
                        test_modules: OnceLock::new(),
                    };
                    found.extend(structure::findings(structure_rules, &context, severity)?);
                }
                Ok(found)
            };
            let mut found = Vec::new();
            if request.workspace_packages.is_empty() {
                found.extend(graph(&import_rules, &structure_rules, &model)?);
            } else {
                // Every architecture of a uv workspace judges only its own files.
                let scoped =
                    |rule: &&CatalogRule| ARCHITECTURE_SCOPED.contains(&rule.code.as_str());
                let (scoped_imports, shared_imports): (Vec<&CatalogRule>, Vec<&CatalogRule>) =
                    import_rules.iter().copied().partition(scoped);
                let (scoped_structure, shared_structure): (Vec<&CatalogRule>, Vec<&CatalogRule>) =
                    structure_rules.iter().copied().partition(scoped);
                found.extend(graph(&shared_imports, &shared_structure, &model)?);
                if !scoped_imports.is_empty() || !scoped_structure.is_empty() {
                    let members: Vec<&str> = request
                        .workspace_packages
                        .keys()
                        .map(String::as_str)
                        .filter(|path| !path.is_empty())
                        .collect();
                    let owner = |path: &str| {
                        members
                            .iter()
                            .copied()
                            .filter(|member| {
                                path == *member
                                    || path
                                        .strip_prefix(member)
                                        .is_some_and(|rest| rest.starts_with('/'))
                            })
                            .max_by_key(|member| member.len())
                            .unwrap_or("")
                    };
                    for (scope, part) in request.workspace_packages.keys().zip(model.parts()) {
                        found.extend(
                            graph(&scoped_imports, &scoped_structure, part)?
                                .into_iter()
                                .filter(|violation| owner(&violation.path) == scope),
                        );
                    }
                }
            }
            outcomes.push(found);
        }
        // Test-quality exclusions only exempt tests from the test rules.
        let test_pairs: Vec<(&SourceFile, &ParsedSource)> = all_test_pairs
            .iter()
            .copied()
            .filter(|(test, _)| !quality_exclude.iter().any(|item| item.matches(&test.path)))
            .collect();
        let test_modules = if test_rules.is_empty() {
            None
        } else {
            Some(profile::time("test syntax", || {
                test_syntax::analyze(&source_pairs, &test_pairs)
            }))
        };
        if let Some(modules) = &test_modules {
            let _span = profile::span("test rules");
            outcomes.par_extend(
                modules
                    .par_iter()
                    .map(|module| analysis.test_file(&test_rules, module, &test_project)),
            );
        }
        let found = dependencies::deduplicate(outcomes.into_iter().flatten().collect());
        let mut by_path: HashMap<String, Vec<Violation>> = HashMap::new();
        for violation in found {
            if !ignores.covers(&violation) {
                by_path
                    .entry(violation.path.clone())
                    .or_default()
                    .push(violation);
            }
        }
        let parsed_by_path: HashMap<&str, &ParsedSource> = source_pairs
            .iter()
            .chain(&all_test_pairs)
            .map(|(file, parsed)| (file.path.as_str(), *parsed))
            .collect();
        let python_files: Vec<(&SourceFile, Vec<Violation>)> = sources
            .iter()
            .chain(&all_tests)
            .map(|file| (*file, by_path.remove(&file.path).unwrap_or_default()))
            .collect();
        let settle_span = profile::span("suppressions");
        let settled: Vec<(Vec<Violation>, usize)> = python_files
            .into_par_iter()
            .map(|(file, found)| {
                if found.is_empty() && !audit.is_active() {
                    return (found, 0);
                }
                match parsed_by_path.get(file.path.as_str()) {
                    Some(parsed) => settle(parsed, &file.path, found, &audit, &names),
                    // Tests no rule read are parsed only for their suppressions.
                    None => match unparsed_suppressions(file) {
                        Some(parsed) => settle(&parsed, &file.path, found, &audit, &names),
                        None => (found, 0),
                    },
                }
            })
            .collect();
        drop(settle_span);
        let mut suppressed = 0;
        // YAML files and package directories hold no suppression comments.
        let mut violations: Vec<Violation> = by_path.into_values().flatten().collect();
        for (found, hidden) in settled {
            violations.extend(found);
            suppressed += hidden;
        }
        // The text each fix was planned against, so applying it can tell an edit since.
        let digests: BTreeMap<String, String> = violations
            .iter()
            .filter(|violation| violation.fix.is_some())
            .filter_map(|violation| {
                parsed_by_path.get(violation.path.as_str()).map(|parsed| {
                    (
                        violation.path.clone(),
                        analysis::dump::sha256_hex(&parsed.text.text),
                    )
                })
            })
            .collect();
        // Configuration findings come after suppressions, which cannot reach YAML.
        let configuration_span = profile::span("configuration findings");
        let mut configuration = Vec::new();
        if let Some((rule, severity)) = meta_rule("MET004").filter(|_| configuration_findings) {
            let inputs = PolicyInputs {
                catalog: &catalog,
                settings: &settings,
                contracts: &contracts,
                files: &files,
                request,
                configured: &configured,
            };
            configuration.extend(missing_rule_policies(&inputs, rule, severity));
        }
        if let Some((rule, severity)) = meta_rule("MET005").filter(|_| configuration_findings)
            && configured.contains("IMP004")
        {
            let inputs = FacadeInputs {
                catalog: &catalog,
                settings: &settings,
                contracts: &contracts,
                files: &files,
                sources: &source_pairs,
                test_roots: &request.project.test_roots,
                include_type_checking: request.architecture.import_graph.include_type_checking,
                limit: request.max_uncovered_facade_callers,
            };
            let evidence: Vec<Violation> = uncovered_imports(&inputs)
                .into_iter()
                .filter(|evidence| !ignores.covers(evidence))
                .collect();
            configuration.extend(uncovered_facades(&inputs, &evidence, rule, severity));
        }
        violations.extend(
            configuration
                .into_iter()
                .filter(|violation| !ignores.covers(violation)),
        );
        drop(configuration_span);
        let coverage_span = profile::span("coverage report");
        let coverage = coverage_report::facts(&ReportInputs {
            request,
            files: &files,
            model: &model,
            contracts: &contracts,
            targets: &targets,
            sources: &source_pairs,
            ordering: report_ordering,
            test_modules: test_modules.as_deref().filter(|_| report_test_quality),
            coverage: &shared_coverage,
        })?;
        drop(coverage_span);
        Ok::<_, String>((violations, suppressed, coverage, digests))
    })()?;
    let _span = profile::span("build report");
    violations.sort_by(Violation::compare);
    // In a uv workspace every finding names the member that owns its file.
    let members = if request.project.uv_workspace {
        Some(analysis::workspace::member_paths(&files)?)
    } else {
        None
    };
    let report: Vec<_> = violations
        .iter()
        .map(|violation| {
            let mut data = violation.to_json();
            // Configuration findings are judged after members are assigned, so they have none.
            let owned = !violation.path.is_empty()
                && !CONFIGURATION_FINDINGS.contains(&violation.code.as_str());
            if let Some(members) = members.as_ref().filter(|_| owned) {
                data["member"] = json!(analysis::workspace::member_for_path(
                    members,
                    &violation.path
                ));
            }
            data
        })
        .collect();
    let mut paths: Vec<String> = files
        .sources
        .values()
        .map(|source| source.path.clone())
        .chain(files.tests.keys().cloned())
        .collect();
    if request.file_naming.checks_yaml() {
        paths.extend(files.yaml_paths());
    }
    Ok(Outcome {
        violations: report,
        suppressed,
        coverage,
        modules: files.sources.len(),
        paths,
        digests,
        baseline: None,
        files: files.inventory(request.file_naming.checks_yaml()),
        ownership_issues,
    })
}

/// Run `work` on a pool whose workers have all exited by the time it returns.
///
/// AST walks recurse, and a long operator chain nests as deep as it is long, so
/// workers get large stacks. Dropping a pool only asks them to stop, and on Windows
/// `ExitProcess` can then kill one inside the heap allocator, which leaves the
/// shutdown spinning forever. Scoped threads do not prevent this because a scope
/// can end before their thread-local teardown, so each OS thread is joined here.
fn with_analysis_pool<R: Send>(work: impl FnOnce() -> R + Send) -> Result<R, String> {
    let mut workers = Vec::new();
    // Workers carry the caller's name, so the profile tells a base revision's work apart.
    let scope = std::thread::current().name().unwrap_or("main").to_owned();
    let pool = rayon::ThreadPoolBuilder::new()
        .spawn_handler(|thread| {
            let worker = std::thread::Builder::new()
                .name(format!("{scope}-{}", thread.index()))
                .stack_size(ANALYSIS_STACK_BYTES)
                .spawn(move || thread.run())?;
            workers.push(worker);
            Ok(())
        })
        .build()
        .map_err(|error| error.to_string())?;
    let result = pool.install(work);
    let _span = profile::span("stop analysis pool");
    drop(pool);
    for worker in workers {
        worker
            .join()
            .map_err(|_| "an analysis worker panicked".to_owned())?;
    }
    Ok(result)
}

struct Analysis<'a> {
    active: &'a [&'a CatalogRule],
    project: rules::Project<'a>,
    settings: &'a Settings<'a>,
    selected: bool,
}

impl Analysis<'_> {
    fn file(&self, source: &SourceFile, parsed: &ParsedSource) -> Vec<Violation> {
        let mut found = Vec::new();
        for rule in self.active {
            for mut violation in rules::check(rule, source, parsed, self.project) {
                let severity = self.settings.severity(
                    rule,
                    self.project.contracts,
                    &source.module,
                    &source.path,
                    self.selected,
                );
                if let Some(severity) = severity {
                    violation.severity = severity.to_owned();
                    found.push(violation);
                }
            }
        }
        found
    }

    /// Source rules over a test module, which belongs to no package.
    fn test_source(
        &self,
        rules: &[&CatalogRule],
        test: &SourceFile,
        parsed: &ParsedSource,
    ) -> Vec<Violation> {
        let mut found = Vec::new();
        for rule in rules {
            let severity =
                self.settings
                    .severity(rule, self.project.contracts, "", &test.path, self.selected);
            let Some(severity) = severity else {
                continue;
            };
            for mut violation in rules::check(rule, test, parsed, self.project) {
                violation.severity = severity.to_owned();
                violation.source_module = None;
                found.push(violation);
            }
        }
        found
    }

    fn test_file(
        &self,
        test_rules: &[&CatalogRule],
        module: &TestModule<'_>,
        project: &TestProject<'_>,
    ) -> Vec<Violation> {
        let mut found = Vec::new();
        for rule in test_rules {
            // A test belongs to no package, so package settings do not reach it.
            let severity = self.settings.severity(
                rule,
                self.project.contracts,
                "",
                module.path,
                self.selected,
            );
            let Some(severity) = severity else {
                continue;
            };
            for mut violation in testing::check(rule, module, project) {
                violation.severity = severity.to_owned();
                found.push(violation);
            }
        }
        found
    }
}

/// Apply the inline suppressions of one file and judge them with the meta rules.
fn settle(
    parsed: &ParsedSource,
    path: &str,
    mut found: Vec<Violation>,
    audit: &SuppressionAudit<'_>,
    names: &HashMap<&str, &str>,
) -> (Vec<Violation>, usize) {
    if !parsed.text.text.contains("smelt:") {
        return (found, 0);
    }
    let suppressions: Vec<Suppression> = suppressions::parse(parsed)
        .into_iter()
        .map(|item| item.resolve_names(names))
        .collect();
    let mut usage: Vec<Vec<String>> = vec![Vec::new(); suppressions.len()];
    found.retain(|violation| {
        if violation.code == STRICT_COVERAGE {
            return true;
        }
        match suppressions.iter().position(|item| item.matches(violation)) {
            Some(index) => {
                usage[index].push(violation.code.clone());
                false
            }
            None => true,
        }
    });
    let hidden = usage.iter().map(Vec::len).sum();
    if audit.is_active() {
        found.extend(audit.findings(path, &parsed.text, &suppressions, &usage));
    }
    (found, hidden)
}

/// A file read only for its suppression comments; `None` when it has none or
/// cannot be tokenized, as `tokenize` errors leave Python without suppressions.
fn unparsed_suppressions(file: &SourceFile) -> Option<ParsedSource> {
    let bytes = vfs::read(&file.absolute).ok()?;
    if !bytes.windows(6).any(|window| window == b"smelt:") {
        return None;
    }
    ParsedSource::read(file).ok()
}

impl Analysis<'_> {}

/// Parse every source; one that does not parse is an error when `strict`.
fn parse_sources(files: &[&SourceFile], strict: bool) -> Result<Vec<ParsedSource>, String> {
    let parsed: Vec<ParsedSource> = files
        .par_iter()
        .map(|file| ParsedSource::read_tolerant(file))
        .collect::<Result<_, _>>()?;
    if strict && let Some(error) = parsed.iter().find_map(|item| item.syntax_error.clone()) {
        return Err(error);
    }
    Ok(parsed)
}

fn parse_all(files: &[&SourceFile]) -> Result<Vec<ParsedSource>, String> {
    files
        .par_iter()
        .map(|file| ParsedSource::read(file))
        .collect()
}

fn path_patterns(patterns: &[String]) -> Result<Vec<PathPattern>, String> {
    patterns
        .iter()
        .map(|pattern| PathPattern::new(pattern))
        .collect()
}

struct Ignores {
    entries: Vec<IgnoreRule>,
}

struct IgnoreRule {
    codes: BTreeSet<String>,
    modules: Vec<ModulePattern>,
    paths: Vec<PathPattern>,
}

impl Ignores {
    fn new(request: &Request, settings: &Settings) -> Result<Self, String> {
        let mut entries = Vec::with_capacity(request.ignore_entries.len());
        for entry in &request.ignore_entries {
            entries.push(IgnoreRule {
                codes: settings.matching_codes(&entry.rule),
                modules: entry
                    .modules
                    .iter()
                    .map(|pattern| ModulePattern::new(pattern))
                    .collect::<Result<_, _>>()?,
                paths: entry
                    .paths
                    .iter()
                    .map(|pattern| PathPattern::new(pattern))
                    .collect::<Result<_, _>>()?,
            });
        }
        Ok(Self { entries })
    }

    fn covers(&self, violation: &Violation) -> bool {
        // Strict coverage cannot be ignored; reasoned exclusions are the only way out.
        violation.code != STRICT_COVERAGE
            && self.entries.iter().any(|entry| {
                entry.codes.contains(&violation.code)
                    && (violation
                        .source_module
                        .as_deref()
                        .is_some_and(|module| entry.modules.iter().any(|p| p.matches(module)))
                        || entry.paths.iter().any(|p| p.matches(&violation.path)))
            })
    }
}

#[cfg(test)]
mod tests {
    use super::with_analysis_pool;
    use std::cell::RefCell;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static EXITED: AtomicUsize = AtomicUsize::new(0);

    struct CountOnExit;

    impl Drop for CountOnExit {
        fn drop(&mut self) {
            EXITED.fetch_add(1, Ordering::SeqCst);
        }
    }

    thread_local! {
        static GUARD: RefCell<Option<CountOnExit>> = const { RefCell::new(None) };
    }

    #[test]
    fn analysis_pool_workers_have_exited_when_it_returns() {
        let workers = with_analysis_pool(|| {
            rayon::broadcast(|_| GUARD.with(|guard| *guard.borrow_mut() = Some(CountOnExit)));
            rayon::current_num_threads()
        })
        .expect("the pool starts");

        assert_eq!(EXITED.load(Ordering::SeqCst), workers);
    }
}
