//! The Rust analysis core. It stays behind `--internal-core` until its reports
//! match the Python engine, which `scripts/compare_rust_core.py` verifies.
mod analysis;
mod config;
mod contracts;
mod files;
mod import_report;
mod patterns;
mod rules;
mod settings;
mod source;
mod suppressions;
mod violation;

use analysis::architecture::{ArchitectureModel, Scope};
use analysis::imports::ImportIndex;
use analysis::python_target::PythonTargets;
use analysis::test_syntax::{self, TestModule};
use config::Request;
use contracts::Contracts;
use files::{FileIndex, SourceFile};
use patterns::{ModulePattern, PathPattern};
use rayon::prelude::*;
use rules::dependencies::{self, GraphContext, IMPORT_RULES, TEST_IMPORT_RULES};
use rules::structure::{self, STRUCTURE_RULES, StructureContext, TEST_READING_RULES};
use rules::testing::{self, TEST_RULES, TestProject};
use serde_json::json;
use settings::{CatalogRule, Settings};
use source::ParsedSource;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::io::{self, Write};
use std::path::Path;
use std::sync::OnceLock;
use suppressions::Suppression;
use violation::Violation;

const CATALOG: &str = include_str!("../../smelt/rules/catalog.json");
const ANALYSIS_STACK_BYTES: usize = 256 * 1024 * 1024;
/// The strict-coverage guard, which ignores and suppressions cannot hide.
const STRICT_COVERAGE: &str = "ARC013";
/// The rules each architecture of a uv workspace runs for its own files.
const ARCHITECTURE_SCOPED: [&str; 8] = [
    "ARC001", "ARC002", "ARC003", "ARC004", "ARC005", "ARC006", "ARC011", "ARC012",
];

/// Check the project of `config` with the comma-separated rules and print JSON.
pub fn serve(config: &Path, select: &str) -> i32 {
    let select = select.split(',').map(str::to_owned).collect();
    match run(config, select).and_then(|output| write_output(&output)) {
        Ok(()) => 0,
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

/// Print the resolved import graph of `config` as JSON.
pub fn serve_imports(config: &Path) -> i32 {
    match import_report::run(config).and_then(|text| write_output(&text)) {
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

fn run(config: &Path, select: Vec<String>) -> Result<String, String> {
    let request = Request::load(config, select)?;
    if !request.members.is_empty() {
        return Err("workspace members are not supported yet".to_owned());
    }
    let catalog: Vec<CatalogRule> =
        serde_json::from_str(CATALOG).map_err(|error| error.to_string())?;
    let unported: Vec<&str> = request
        .select
        .iter()
        .map(String::as_str)
        .filter(|code| !rules::PORTED.contains(code))
        .collect();
    if request.select.is_empty() || !unported.is_empty() {
        return Err(format!("rules not ported: {}", unported.join(", ")));
    }
    let files = FileIndex::discover(&request)?;
    let contracts = Contracts::resolve(&request, &files)?;
    let settings = Settings::new(&catalog, &request);
    let active = settings.active(&request.select);
    let ignores = Ignores::new(&request, &settings)?;
    let names: HashMap<&str, &str> = catalog
        .iter()
        .map(|rule| (rule.name.as_str(), rule.code.as_str()))
        .collect();
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
        ignores: &ignores,
        names: &names,
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
    let outcomes = with_analysis_pool(|| {
        let parsed = parse_all(&sources)?;
        let source_pairs: Vec<(&SourceFile, &ParsedSource)> =
            sources.iter().copied().zip(&parsed).collect();
        let imports_tests = import_rules
            .iter()
            .any(|rule| TEST_IMPORT_RULES.contains(&rule.code.as_str()));
        let reads_tests = !test_rules.is_empty()
            || imports_tests
            || structure_rules
                .iter()
                .any(|rule| TEST_READING_RULES.contains(&rule.code.as_str()));
        let parsed_tests = if reads_tests {
            parse_all(&all_tests)?
        } else {
            Vec::new()
        };
        let all_test_pairs: Vec<(&SourceFile, &ParsedSource)> =
            all_tests.iter().copied().zip(&parsed_tests).collect();
        let mut outcomes: Vec<_> = source_pairs
            .par_iter()
            .map(|(source, parsed)| analysis.file(source, parsed))
            .collect();
        if !import_rules.is_empty() || !structure_rules.is_empty() {
            let imported_tests: &[(&SourceFile, &ParsedSource)] =
                if imports_tests { &all_test_pairs } else { &[] };
            let imports = if import_rules.is_empty() {
                None
            } else {
                Some(ImportIndex::build(
                    &files,
                    &request.project.root_packages,
                    &source_pairs,
                    imported_tests,
                )?)
            };
            let graph = |import_rules: &[&CatalogRule],
                         structure_rules: &[&CatalogRule],
                         model: &ArchitectureModel<'_>|
             -> Result<Vec<Violation>, String> {
                let severity = |rule, module: &str| {
                    settings.severity(rule, &contracts, module, analysis.selected)
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
                        workspace_packages: &request.workspace_packages,
                        quality_exclude: &quality_exclude,
                        member_usage: OnceLock::new(),
                        test_modules: OnceLock::new(),
                    };
                    found.extend(structure::findings(structure_rules, &context, severity)?);
                }
                Ok(found)
            };
            let roots = &request.project.root_packages;
            let mut found = Vec::new();
            if request.workspace_packages.is_empty() {
                let model =
                    ArchitectureModel::build(&request.architecture, roots, &files, Scope::Project);
                found.extend(graph(&import_rules, &structure_rules, &model)?);
            } else {
                // Every architecture of a uv workspace judges only its own files.
                let scoped =
                    |rule: &&CatalogRule| ARCHITECTURE_SCOPED.contains(&rule.code.as_str());
                let (scoped_imports, shared_imports): (Vec<&CatalogRule>, Vec<&CatalogRule>) =
                    import_rules.iter().copied().partition(scoped);
                let (scoped_structure, shared_structure): (Vec<&CatalogRule>, Vec<&CatalogRule>) =
                    structure_rules.iter().copied().partition(scoped);
                let root_packages = request
                    .workspace_packages
                    .get("")
                    .map_or(&[][..], Vec::as_slice);
                let combined = ArchitectureModel::build(
                    &request.architecture,
                    roots,
                    &files,
                    Scope::Combined(root_packages),
                );
                found.extend(graph(&shared_imports, &shared_structure, &combined)?);
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
                    for (scope, packages) in &request.workspace_packages {
                        let architecture = if scope.is_empty() {
                            &request.architecture
                        } else {
                            &request.member_architecture
                        };
                        let part = ArchitectureModel::build(
                            architecture,
                            roots,
                            &files,
                            Scope::Part(packages),
                        );
                        found.extend(
                            graph(&scoped_imports, &scoped_structure, &part)?
                                .into_iter()
                                .filter(|violation| owner(&violation.path) == scope),
                        );
                    }
                }
            }
            let found = dependencies::deduplicate(found);
            let parsed_files: Vec<(&SourceFile, &ParsedSource)> = source_pairs
                .iter()
                .chain(&all_test_pairs)
                .copied()
                .collect();
            outcomes.extend(settle_by_file(&analysis, &parsed_files, found));
        }
        if !test_rules.is_empty() {
            // Test-quality exclusions only exempt tests from the test rules.
            let test_pairs: Vec<(&SourceFile, &ParsedSource)> = all_test_pairs
                .iter()
                .copied()
                .filter(|(test, _)| !quality_exclude.iter().any(|item| item.matches(&test.path)))
                .collect();
            let modules = test_syntax::analyze(&source_pairs, &test_pairs);
            outcomes.par_extend(
                modules
                    .par_iter()
                    .map(|module| analysis.test_file(&test_rules, module, &test_project)),
            );
        }
        Ok::<_, String>(outcomes)
    })??;
    let mut suppressed = 0;
    let mut violations = Vec::new();
    for (found, hidden) in outcomes {
        violations.extend(found);
        suppressed += hidden;
    }
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
            if let Some(members) = members.as_ref().filter(|_| !violation.path.is_empty()) {
                data["member"] = json!(analysis::workspace::member_for_path(
                    members,
                    &violation.path
                ));
            }
            data
        })
        .collect();
    Ok(json!({"violations": report, "suppressed": suppressed}).to_string())
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
    let pool = rayon::ThreadPoolBuilder::new()
        .spawn_handler(|thread| {
            let worker = std::thread::Builder::new()
                .stack_size(ANALYSIS_STACK_BYTES)
                .spawn(move || thread.run())?;
            workers.push(worker);
            Ok(())
        })
        .build()
        .map_err(|error| error.to_string())?;
    let result = pool.install(work);
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
    ignores: &'a Ignores,
    names: &'a HashMap<&'a str, &'a str>,
    selected: bool,
}

impl Analysis<'_> {
    fn file(&self, source: &SourceFile, parsed: &ParsedSource) -> (Vec<Violation>, usize) {
        let mut found = Vec::new();
        for rule in self.active {
            for mut violation in rules::check(rule, source, parsed, self.project) {
                let severity = self.settings.severity(
                    rule,
                    self.project.contracts,
                    &source.module,
                    self.selected,
                );
                if let Some(severity) = severity {
                    violation.severity = severity.to_owned();
                    found.push(violation);
                }
            }
        }
        self.settle(parsed, found)
    }

    fn test_file(
        &self,
        test_rules: &[&CatalogRule],
        module: &TestModule<'_>,
        project: &TestProject<'_>,
    ) -> (Vec<Violation>, usize) {
        let mut found = Vec::new();
        for rule in test_rules {
            // A test belongs to no package, so package settings do not reach it.
            let severity = self
                .settings
                .severity(rule, self.project.contracts, "", self.selected);
            let Some(severity) = severity else {
                continue;
            };
            for mut violation in testing::check(rule, module, project) {
                violation.severity = severity.to_owned();
                found.push(violation);
            }
        }
        self.settle(module.parsed, found)
    }

    /// Apply ignores and inline suppressions to the findings of one file.
    fn settle(&self, parsed: &ParsedSource, mut found: Vec<Violation>) -> (Vec<Violation>, usize) {
        found.retain(|violation| !self.ignores.covers(violation));
        if found.is_empty() || !parsed.text.text.contains("smelt:") {
            return (found, 0);
        }
        let suppressions: Vec<Suppression> = suppressions::parse(parsed)
            .into_iter()
            .map(|item| item.resolve_names(self.names))
            .collect();
        let before = found.len();
        found.retain(|violation| {
            violation.code == STRICT_COVERAGE
                || !suppressions.iter().any(|item| item.matches(violation))
        });
        let hidden = before - found.len();
        (found, hidden)
    }
}

/// Apply ignores and suppressions to project-wide findings, file by file.
fn settle_by_file(
    analysis: &Analysis<'_>,
    sources: &[(&SourceFile, &ParsedSource)],
    found: Vec<Violation>,
) -> Vec<(Vec<Violation>, usize)> {
    let parsed: HashMap<&str, &ParsedSource> = sources
        .iter()
        .map(|(source, parsed)| (source.path.as_str(), *parsed))
        .collect();
    let mut by_path: BTreeMap<String, Vec<Violation>> = BTreeMap::new();
    for violation in found {
        by_path
            .entry(violation.path.clone())
            .or_default()
            .push(violation);
    }
    by_path
        .into_iter()
        .map(|(path, mut findings)| match parsed.get(path.as_str()) {
            Some(source) => analysis.settle(source, findings),
            // YAML files and package directories hold no suppression comments.
            None => {
                findings.retain(|violation| !analysis.ignores.covers(violation));
                (findings, 0)
            }
        })
        .collect()
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
