//! `smelt check` in the native client, without starting Python.
//!
//! The native command writes the report the Python CLI would write, byte for
//! byte. It takes only the invocations and configurations it supports; for
//! anything else, and for every error, it returns `None` and the launcher runs
//! the Python CLI, which reports the error exactly. `SMELT_NATIVE_CHECK=0`
//! always runs the Python CLI.
mod activation;
mod args;
mod changes;
mod coverage;
mod debt;
mod finding;
mod policy;
mod pyjson;
mod pytext;
mod report;
mod staged;
mod text;
mod validate;

use crate::checker::config::load::{LoadedConfig, load_config, rule_layers};
use crate::checker::config::paths;
use crate::checker::config::workspace::member_directories;
use crate::checker::{CATALOG, check_loaded, profile, vfs};
use args::{Args, Format};
use finding::{Finding, Severity};
use policy::{Catalog, Policy, Resolved, Rule};
use report::{Diagnostic, Report};
use serde_json::{Map, Value, json};
use staged::Staged;
use std::collections::HashMap;
use std::env;
use std::ffi::OsString;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

const CONFIG_FILENAMES: [&str; 2] = ["smelt.yml", "smelt.yaml"];
const COVERAGE: &str = "ARC013";
const RESOLVED_DEBT: &str = "MET003";
/// The rule that imports installed packages from the interpreter's search path.
const INSTALLED_PACKAGES: &str = "IMP004";
/// The report's category order; meta findings judge the run, not a category.
const CATEGORIES: [&str; 10] = [
    "architecture",
    "imports",
    "layout",
    "visibility",
    "modeling",
    "typing",
    "complexity",
    "idioms",
    "frameworks",
    "tests",
];
const EXIT_OK: i32 = 0;
const EXIT_VIOLATIONS: i32 = 1;

/// Run `smelt check` natively; `None` leaves the invocation to the Python CLI.
pub fn check(arguments: &[OsString]) -> Option<i32> {
    if env::var_os("SMELT_NATIVE_CHECK").is_some_and(|value| value == "0") {
        return None;
    }
    let Some(args) = args::parse(arguments) else {
        trace("an option the native check does not take");
        return None;
    };
    profile::start();
    let output = profile::time("native check", || {
        // One set of directory listings serves the whole check, from the
        // configuration's workspace scan to the report's validation.
        let _listings = vfs::ListingCache::enable();
        run(&args)
    });
    profile::report();
    let (text, code) = output.map_err(trace).ok()?;
    let text = if cfg!(windows) {
        text.replace('\n', "\r\n")
    } else {
        text
    };
    let mut stdout = io::stdout().lock();
    // A closed pipe ends the output; the exit code still reports the check.
    let _ = stdout
        .write_all(text.as_bytes())
        .and_then(|()| stdout.flush());
    Some(code)
}

fn locate(cwd: &Path, explicit: Option<&str>) -> Option<PathBuf> {
    if let Some(config) = explicit {
        let path = cwd.join(config);
        return vfs::is_file(&path).then_some(path);
    }
    paths::resolve(cwd).ancestors().find_map(|directory| {
        CONFIG_FILENAMES
            .iter()
            .map(|name| directory.join(name))
            .find(|candidate| vfs::is_file(candidate))
    })
}

/// `_split_selectors`.
fn selectors(raw: Option<&str>) -> Vec<String> {
    raw.into_iter()
        .flat_map(|raw| raw.split(','))
        .map(|code| code.trim_matches(pytext::is_space))
        .filter(|code| !code.is_empty())
        .map(str::to_owned)
        .collect()
}

/// `_scope`: the project-relative paths a check reports, `()` for all.
fn scope(loaded: &LoadedConfig, root: &Path, cwd: &Path, raw: &[String]) -> Option<Vec<String>> {
    if let Some(member) = loaded.member.as_ref().filter(|_| raw.is_empty()) {
        // Started in a workspace member: report that member's files only.
        return Some(vec![member.clone()]);
    }
    if raw.iter().any(|path| {
        let resolved = paths::resolve(&cwd.join(path));
        loaded
            .inputs
            .iter()
            .any(|input| paths::same_path(input, &resolved))
    }) {
        return Some(Vec::new());
    }
    let scoped: Vec<String> = raw
        .iter()
        .map(|path| validate::relative_scope(root, cwd, path))
        .collect::<Option<_>>()?;
    let config = validate::relative_scope(root, cwd, &loaded.path.to_string_lossy())?;
    Some(if scoped.contains(&config) {
        Vec::new()
    } else {
        scoped
    })
}

/// The text of a project file's lines, read once per file, as
/// `FileIndex.lines` decodes them.
struct Lines<'a> {
    root: &'a Path,
    files: HashMap<String, Option<Vec<String>>>,
}

impl Lines<'_> {
    fn line(&mut self, path: &str, number: u64) -> Result<String, ()> {
        let root = self.root;
        let lines = self
            .files
            .entry(path.to_owned())
            .or_insert_with(|| decoded_lines(&root.join(path)))
            .as_ref()
            .ok_or(())?;
        let index = usize::try_from(number).map_err(|_| ())?;
        Ok(if index > 0 && index <= lines.len() {
            lines[index - 1].clone()
        } else {
            String::new()
        })
    }

    fn snippet(&mut self, finding: &Finding) -> Result<String, ()> {
        match (finding.path.as_deref(), finding.line) {
            (Some(path), Some(line)) if !path.is_empty() && line != 0 => self.line(path, line),
            _ => Ok(String::new()),
        }
    }
}

/// `read_text(encoding="utf-8-sig").splitlines()`.
fn decoded_lines(path: &Path) -> Option<Vec<String>> {
    let text = paths::read_text(path).ok()?;
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
    Some(lines)
}

struct Debt {
    in_debt: usize,
    findings: Vec<Finding>,
}

/// A debt file and the workspace member whose findings it may accept.
#[derive(PartialEq)]
struct DebtScope {
    path: String,
    member: Option<String>,
}

/// `debt_scopes`: each member's debt file, the deepest member first, then the root's.
fn debt_scopes(config: &Value) -> Vec<DebtScope> {
    let mut members: Vec<(&String, &str)> = config["members"]
        .as_object()
        .into_iter()
        .flatten()
        .filter_map(|(path, member)| member["debt"].as_str().map(|debt| (path, debt)))
        .collect();
    members.sort_by_key(|(path, _)| std::cmp::Reverse(path.len()));
    let mut scopes: Vec<DebtScope> = members
        .into_iter()
        .map(|(path, debt)| DebtScope {
            path: if debt.starts_with('/') {
                debt.to_owned()
            } else if path.is_empty() || path.ends_with('/') {
                format!("{path}{debt}")
            } else {
                format!("{path}/{debt}")
            },
            member: Some(path.clone()),
        })
        .collect();
    if let Some(debt) = config["debt"].as_str().filter(|debt| !debt.is_empty()) {
        scopes.push(DebtScope {
            path: debt.to_owned(),
            member: None,
        });
    }
    scopes
}

/// `debt_scope_for_path`: the closest member's debt file, else the root's.
fn debt_scope_for_path<'s>(scopes: &'s [DebtScope], path: Option<&str>) -> Option<&'s DebtScope> {
    scopes.iter().find(|scope| match (&scope.member, path) {
        (None, _) => true,
        (Some(member), Some(path)) => {
            path == member
                || path
                    .strip_prefix(member.as_str())
                    .is_some_and(|rest| rest.starts_with('/'))
        }
        (Some(_), None) => false,
    })
}

/// What matching findings against the debt files reads.
struct DebtInputs<'a> {
    loaded: &'a LoadedConfig,
    policy: &'a Policy<'a>,
    catalog: &'a Catalog,
    resolved: &'a [Resolved],
    sources: &'a HashMap<String, String>,
    active: &'a HashMap<&'a str, Severity>,
    args: &'a Args,
    /// Whether the check covers only part of the project.
    partial: bool,
    staged: Option<&'a Staged>,
}

fn match_debt(
    inputs: &DebtInputs<'_>,
    mut findings: Vec<Finding>,
    lines: &mut Lines,
) -> Option<Debt> {
    let DebtInputs {
        loaded,
        policy,
        catalog,
        resolved,
        sources,
        active,
        args,
        partial,
        staged,
    } = *inputs;
    let scopes = if args.no_debt {
        Vec::new()
    } else {
        debt_scopes(&loaded.config)
    };
    let root = loaded.path.parent()?;
    let selected = args
        .select
        .as_deref()
        .is_some_and(|select| !selectors(Some(select)).is_empty());
    let mut in_debt = 0;
    for scope in &scopes {
        let file = root.join(&scope.path);
        // A staged check reads the debt the index stages.
        let bytes = match staged {
            Some(staged) => staged.read(&file).transpose().ok()?,
            None => vfs::is_file(&file)
                .then(|| vfs::read(&file))
                .transpose()
                .ok()?,
        };
        let entries = match bytes {
            Some(bytes) => debt::parse(std::str::from_utf8(&bytes).ok()?).ok()?,
            None => Vec::new(),
        };
        let mut kept = Vec::new();
        for entry in entries {
            // Inactive rules have not been checked; their debt is not resolved.
            if debt_scope_for_path(&scopes, entry.path.as_deref()) != Some(scope)
                || !active.contains_key(entry.code.as_str())
            {
                continue;
            }
            let rule = catalog.by_code(&entry.code)?;
            if policy
                .severity_for_path(rule, resolved, sources, entry.path.as_deref(), selected)
                .ok()?
                .is_some()
            {
                kept.push(entry);
            }
        }
        let matched =
            debt::match_findings(kept, findings, &mut |finding| lines.snippet(finding)).ok()?;
        findings = matched.new;
        in_debt += matched.known;
        // A scoped or `--changed` check cannot tell what the rest resolved.
        if let Some(severity) = active.get(RESOLVED_DEBT).filter(|_| !partial) {
            let rule = catalog.by_code(RESOLVED_DEBT)?;
            findings.extend(matched.resolved.iter().map(|entry| Finding {
                code: rule.code.clone(),
                rule: rule.name.clone(),
                severity: *severity,
                message: format!(
                    "resolved debt entry: {} {} ({})",
                    entry.code,
                    entry.path.as_deref().unwrap_or(""),
                    entry.message
                ),
                path: Some(scope.path.clone()),
                line: None,
                column: None,
                end_line: None,
                end_column: None,
                source_module: None,
                target_module: None,
                import_chain: Vec::new(),
                feature: None,
                layer: None,
                expected: None,
                hint: Some("Run `smelt debt --prune` to remove resolved entries.".to_owned()),
                edge: None,
                docs_url: Some(finding::docs_url(&rule.code)),
                category: Some(rule.category.clone()),
                member: scope.member.clone(),
                fix: None,
            }));
        }
    }
    Some(Debt { in_debt, findings })
}

fn trace(reason: &str) {
    if env::var_os("SMELT_TRACE").is_some() {
        eprintln!("smelt trace: the Python CLI checks: {reason}");
    }
}

/// The report text and exit code, or why the Python CLI runs instead.
fn run(args: &Args) -> Result<(String, i32), &'static str> {
    let cwd = env::current_dir().map_err(|_| "no working directory")?;
    let explicit = args.config.as_deref().filter(|config| !config.is_empty());
    // A staged check reads its project, configuration included, from the index.
    let staged = if args.staged {
        Some(profile::time("capture staged index", || {
            Staged::capture(&cwd)
        })?)
    } else {
        None
    };
    let (here, config_path) = match &staged {
        Some(staged) => {
            let here = staged.locate(&cwd)?;
            // Unlike a working-tree check, `--config ""` names the directory itself.
            let config = match args.config.as_deref() {
                Some(config) => {
                    let path = staged.locate(&staged::lexical(&cwd.join(config)))?;
                    vfs::is_file(&path).then_some(path)
                }
                None => locate(&here, None),
            }
            .filter(|path| staged.contains(path))
            .ok_or("no configuration file in the staged index")?;
            (here, config)
        }
        None => {
            let config = locate(&cwd, explicit).ok_or("no configuration file")?;
            (cwd.clone(), config)
        }
    };
    let mut loaded = profile::time("load configuration", || load_config(&config_path))
        .map_err(|_| "a configuration error")?;
    if args.config.as_deref().is_none_or(str::is_empty)
        && loaded.config["project"]["workspace"] == "uv"
    {
        // Started inside a workspace member: the check reports that member.
        let root = loaded.path.parent().ok_or("no project root")?;
        let here = paths::resolve(&here);
        let owner = member_directories(root, false)
            .unwrap_or_default()
            .into_iter()
            .filter(|member| {
                member.skipped.is_none()
                    && here.starts_with(paths::resolve(&root.join(&member.path)))
            })
            .map(|member| member.path)
            .max_by_key(String::len);
        if owner.is_some() {
            loaded.member = owner;
        }
    }
    if loaded.config["plugins"]
        .as_array()
        .is_some_and(|paths| !paths.is_empty())
    {
        return Err("project rule declarations require the Python policy loader");
    }
    let catalog = Catalog::load(CATALOG).ok_or("an unreadable rule catalog")?;
    let root = loaded.path.parent().ok_or("no project root")?.to_path_buf();
    let scope =
        scope(&loaded, &root, &here, &args.paths).ok_or("a check path outside the project")?;
    if let Some(staged) = &staged {
        let debt: Vec<String> = debt_scopes(&loaded.config)
            .into_iter()
            .map(|scope| scope.path)
            .collect();
        staged.validate(&loaded, &debt)?;
    }
    let mut layers: Vec<Value> = rule_layers(&loaded.config).into_iter().cloned().collect();
    let configured_strict = policy::strict_coverage(&layers);
    if args.strict_coverage && !configured_strict {
        layers.push(json!({"architecture/uncovered-source": "error"}));
    }
    let policy =
        Policy::new(&catalog, &loaded.config, &layers).map_err(|()| "invalid rule settings")?;
    let select = policy
        .selected_codes(&selectors(args.select.as_deref()))
        .map_err(|()| "an unknown --select prefix")?;
    let ignore = policy
        .selected_codes(&selectors(args.ignore.as_deref()))
        .map_err(|()| "an unknown --ignore prefix")?;
    let active = policy
        .active(&select, &ignore)
        .map_err(|()| "invalid strict coverage")?;
    // Python startup can modify sys.path through .pth files and sitecustomize.
    let structure = &loaded.config["__structure__"];
    if active
        .iter()
        .any(|(rule, _)| rule.code == INSTALLED_PACKAGES)
        && std::iter::once(structure)
            .chain(
                structure["contracts"]
                    .as_object()
                    .into_iter()
                    .flat_map(Map::values),
            )
            .any(|policy| {
                policy["third_party_reexports"]
                    .as_array()
                    .is_some_and(|packages| !packages.is_empty())
            })
    {
        return Err("installed package re-exports that need Python's search path");
    }
    let comparison = match &staged {
        Some(staged) => {
            let mut comparison = changes::compare(
                &staged.repository,
                staged.head.clone(),
                args.base.as_deref(),
            )?;
            comparison.staged = true;
            comparison.index_hash = Some(staged.identity.clone());
            Some((staged.repository.clone(), comparison))
        }
        None if args.compares() => Some(changes::resolve(&root, args.base.as_deref())?),
        None => None,
    };
    // The core finds the project in the base revision where it finds it in
    // the checked tree: the snapshot's top level for a staged check.
    let baseline = comparison.as_ref().and_then(|(top, comparison)| {
        comparison.revision.clone().map(|revision| {
            let checked = staged.as_ref().map_or_else(
                || top.clone(),
                |staged| staged.snapshot.tree().to_path_buf(),
            );
            (top.clone(), revision, checked)
        })
    });
    let owned = |codes: &[&str]| {
        codes
            .iter()
            .map(|code| (*code).to_owned())
            .collect::<Vec<_>>()
    };
    let outcome = check_loaded(
        &loaded,
        owned(&select),
        &owned(&ignore),
        args.strict_coverage,
        scope.is_empty() && comparison.is_none(),
        baseline,
    )
    .ok_or("an analysis error")?;
    let _span = profile::span("report");
    if !outcome.ownership_issues.is_empty() {
        return Err("unverified canonical exports");
    }
    let inventory = validate::Inventory::new(&outcome.files);
    let resolved: Vec<Resolved> = outcome.coverage["contracts"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|entry| Resolved {
            name: entry["name"].as_str().unwrap_or_default().to_owned(),
            package: entry["package"].as_str().unwrap_or_default().to_owned(),
        })
        .collect();
    validate::missing_paths(
        &loaded.config,
        &root,
        &inventory,
        &resolved,
        &outcome.features,
    )
    .map_err(|()| "a configured path that matches nothing")?;
    policy
        .validate_packages(&resolved, &inventory)
        .map_err(|()| "conflicting package rules")?;
    validate::scope(&root, &inventory, &scope)
        .map_err(|()| "a check path without checked files")?;
    let findings: Vec<Finding> = outcome
        .violations
        .iter()
        .map(Finding::from_core)
        .collect::<Option<_>>()
        .ok_or("a finding the report model rejects")?;
    let (gaps, findings): (Vec<Finding>, Vec<Finding>) = findings
        .into_iter()
        .partition(|finding| finding.code == COVERAGE);
    let severities: HashMap<&str, Severity> = active
        .iter()
        .map(|(rule, severity)| (rule.code.as_str(), *severity))
        .collect();
    let mut lines = Lines {
        root: &root,
        files: HashMap::new(),
    };
    let inputs = DebtInputs {
        loaded: &loaded,
        policy: &policy,
        catalog: &catalog,
        resolved: &resolved,
        sources: &inventory.sources,
        active: &severities,
        args,
        partial: !scope.is_empty() || comparison.is_some(),
        staged: staged.as_ref(),
    };
    let debt =
        match_debt(&inputs, findings, &mut lines).ok_or("a debt file the Python engine reports")?;
    let mut findings = debt.findings;
    if let Some(before) = outcome.baseline {
        // Only the findings the compared commit did not have are reported.
        let known: Vec<(Finding, String)> = before
            .into_iter()
            .map(|mut finding| {
                let snippet = finding
                    .as_object_mut()?
                    .shift_remove("snippet")?
                    .as_str()?
                    .to_owned();
                Some((Finding::from_core(&finding)?, snippet))
            })
            .collect::<Option<_>>()
            .ok_or("a baseline finding the report model rejects")?;
        findings = debt::match_findings(debt::from_findings(&known), findings, &mut |finding| {
            lines.snippet(finding)
        })
        .map_err(|()| "an unreadable source")?
        .new;
    }
    let in_scope = |finding: &Finding| {
        scope.is_empty() || validate::under_any(finding.path.as_deref(), &scope)
    };
    let mut violations = gaps;
    violations.extend(findings.into_iter().filter(in_scope));
    violations.sort_by(Finding::compare);
    let rules: Vec<&Rule> = active.iter().map(|(rule, _)| *rule).collect();
    let categories = CATEGORIES
        .into_iter()
        .filter(|category| rules.iter().any(|rule| rule.category == *category))
        .collect();
    let mut coverage = match coverage::effective(
        &loaded.config,
        &layers,
        configured_strict || args.strict_coverage,
        outcome.coverage,
    ) {
        Value::Object(coverage) => coverage,
        _ => Map::new(),
    };
    if !args.show_coverage {
        coverage.shift_remove("inventory");
    }
    let config_name = loaded
        .path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut diagnostics: Vec<Diagnostic> = loaded
        .warnings
        .iter()
        .map(|issue| Diagnostic {
            path: issue
                .source
                .clone()
                .filter(|source| !source.is_empty())
                .unwrap_or_else(|| config_name.clone()),
            key: issue.path.clone(),
            message: issue.message.clone(),
        })
        .collect();
    diagnostics.extend(
        policy
            .unused_policies()
            .into_iter()
            .map(|(key, message)| Diagnostic {
                path: config_name.clone(),
                key,
                message,
            }),
    );
    let checked_files = outcome
        .paths
        .iter()
        .filter(|path| scope.is_empty() || validate::under_any(Some(path), &scope))
        .count();
    let report = Report {
        violations,
        modules: outcome.modules as u64,
        categories,
        rules,
        fail_on: if args.fail_on_warning {
            Severity::Warning
        } else {
            Severity::Error
        },
        suppressed: outcome.suppressed as u64,
        in_debt: debt.in_debt,
        scope,
        checked_files,
        comparison: comparison.map(|(_, comparison)| comparison.to_json()),
        coverage,
        project: coverage::project_metadata(&staged.as_ref().map_or_else(
            || loaded.path.clone(),
            |staged| staged.original(&loaded.path),
        )),
        diagnostics,
    };
    let show_hints = args.show_hints || args.compares();
    let text = match args.format() {
        Format::Json => report::render_json(&report, args.show_coverage),
        Format::Sarif => report::render_sarif(&report, env!("CARGO_PKG_VERSION")),
        Format::Github => report::render_github(&report),
        Format::Concise => text::render(
            &report,
            &mut |path, line| lines.line(path, line),
            false,
            true,
            // Hints on the files just changed are few and relevant.
            show_hints,
        )
        .map_err(|()| "an unreadable source")?,
        Format::Text => {
            let color = !args.no_color
                && env::var_os("NO_COLOR").is_none_or(|value| value.is_empty())
                && stdout_is_terminal();
            let mut text = text::render(
                &report,
                &mut |path, line| lines.line(path, line),
                color,
                false,
                show_hints,
            )
            .map_err(|()| "an unreadable source")?;
            if args.show_coverage {
                text.push_str("Coverage inventory:\n");
                for entry in report
                    .coverage
                    .get("inventory")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                {
                    text.push_str(&format!(
                        "  {}: {} — {}\n",
                        pytext::display(&entry["status"]),
                        pytext::display(&entry["path"]),
                        pytext::display(&entry["reason"]),
                    ));
                }
            }
            text
        }
    };
    let code = if report.failed() {
        EXIT_VIOLATIONS
    } else {
        EXIT_OK
    };
    Ok((text, code))
}

/// `sys.stdout.isatty()`: on Windows a character device, as the C runtime's
/// `isatty` reports it, so a Cygwin pipe is not a terminal.
fn stdout_is_terminal() -> bool {
    #[cfg(windows)]
    {
        windows_char_device()
    }
    #[cfg(not(windows))]
    {
        io::IsTerminal::is_terminal(&io::stdout())
    }
}

#[cfg(windows)]
fn windows_char_device() -> bool {
    use std::os::windows::io::AsRawHandle;
    const FILE_TYPE_CHAR: u32 = 2;
    unsafe extern "system" {
        fn GetFileType(handle: *mut std::ffi::c_void) -> u32;
    }
    let handle = io::stdout().as_raw_handle();
    // SAFETY: the handle is the process's standard output, open for its lifetime.
    !handle.is_null() && unsafe { GetFileType(handle.cast()) } == FILE_TYPE_CHAR
}
