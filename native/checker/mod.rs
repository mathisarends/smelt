//! The Rust analysis core. It stays behind `--internal-core` until its reports
//! match the Python engine, which `scripts/compare_rust_core.py` verifies.
mod config;
mod contracts;
mod files;
mod patterns;
mod rules;
mod settings;
mod source;
mod suppressions;
mod violation;

use config::Request;
use contracts::Contracts;
use files::{FileIndex, SourceFile};
use patterns::{ModulePattern, PathPattern};
use rayon::prelude::*;
use serde_json::json;
use settings::{CatalogRule, Settings};
use source::ParsedSource;
use std::collections::{BTreeSet, HashMap};
use std::io::{self, Write};
use std::path::Path;
use suppressions::Suppression;
use violation::Violation;

const CATALOG: &str = include_str!("../../smelt/rules/catalog.json");

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
    let analysis = Analysis {
        active: &active,
        contracts: &contracts,
        settings: &settings,
        ignores: &ignores,
        names: &names,
        selected: !request.select.is_empty(),
    };
    let sources: Vec<&SourceFile> = files.sources.values().collect();
    let outcomes = sources
        .par_iter()
        .map(|source| analysis.file(source))
        .collect::<Result<Vec<_>, String>>()?;
    let mut suppressed = 0;
    let mut violations = Vec::new();
    for (found, hidden) in outcomes {
        violations.extend(found);
        suppressed += hidden;
    }
    violations.sort_by(Violation::compare);
    let report: Vec<_> = violations.iter().map(Violation::to_json).collect();
    Ok(json!({"violations": report, "suppressed": suppressed}).to_string())
}

struct Analysis<'a> {
    active: &'a [&'a CatalogRule],
    contracts: &'a Contracts<'a>,
    settings: &'a Settings<'a>,
    ignores: &'a Ignores,
    names: &'a HashMap<&'a str, &'a str>,
    selected: bool,
}

impl Analysis<'_> {
    fn file(&self, source: &SourceFile) -> Result<(Vec<Violation>, usize), String> {
        let parsed = ParsedSource::read(source)?;
        let mut found = Vec::new();
        for rule in self.active {
            for mut violation in rules::check(rule, source, &parsed, self.contracts) {
                let severity =
                    self.settings
                        .severity(rule, self.contracts, &source.module, self.selected);
                if let Some(severity) = severity {
                    violation.severity = severity.to_owned();
                    found.push(violation);
                }
            }
        }
        found.retain(|violation| !self.ignores.covers(violation));
        if found.is_empty() || !parsed.text.text.contains("smelt:") {
            return Ok((found, 0));
        }
        let suppressions: Vec<Suppression> = suppressions::parse(&parsed)
            .into_iter()
            .map(|item| item.resolve_names(self.names))
            .collect();
        let before = found.len();
        found.retain(|violation| !suppressions.iter().any(|item| item.matches(violation)));
        let hidden = before - found.len();
        Ok((found, hidden))
    }
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
        self.entries.iter().any(|entry| {
            entry.codes.contains(&violation.code)
                && (violation
                    .source_module
                    .as_deref()
                    .is_some_and(|module| entry.modules.iter().any(|p| p.matches(module)))
                    || entry.paths.iter().any(|p| p.matches(&violation.path)))
        })
    }
}
