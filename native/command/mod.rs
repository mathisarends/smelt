//! `smelt check` in the native client, without starting Python.
//!
//! The native command writes the report the Python CLI would write, byte for
//! byte. It takes only the invocations and configurations it supports; for
//! anything else, and for every error, it returns `None` and the launcher runs
//! the Python CLI, which reports the error exactly. `SMELT_NATIVE_CHECK=0`
//! always runs the Python CLI.
mod args;
mod coverage;
mod debt;
mod finding;
mod policy;
mod pyjson;
mod pytext;
mod report;
mod text;
mod validate;

use crate::checker::config::load::{LoadedConfig, load_config, rule_layers};
use crate::checker::config::paths;
use crate::checker::{CATALOG, check_loaded, interpreter_search_path, profile, vfs};
use args::{Args, Format};
use finding::{Finding, Severity};
use policy::{Catalog, Policy, Resolved, Rule};
use report::{Diagnostic, Report};
use serde_json::{Map, Value, json};
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
    let output = profile::time("native check", || run(&args));
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

/// `severity_for_path`'s module: the source at `path`, or the module a path
/// below a source root would be.
fn module_for(
    config: &Value,
    sources: &HashMap<String, String>,
    path: &str,
) -> Result<Option<String>, ()> {
    if let Some(module) = sources.get(path) {
        return Ok(Some(module.clone()));
    }
    let project = &config["project"];
    let roots: Vec<&str> = project["root_packages"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    for source_root in project["source_roots"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        let relative = if source_root == "." {
            Some(path)
        } else if path == source_root {
            Some("")
        } else {
            path.strip_prefix(source_root)
                .and_then(|rest| rest.strip_prefix('/'))
        };
        let Some(relative) = relative else {
            continue;
        };
        if relative.is_empty() || relative == "." {
            // `Path(".").with_suffix("")` raises in the Python engine.
            return Err(());
        }
        let mut parts: Vec<&str> = relative
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .collect();
        if let Some(last) = parts.last_mut() {
            *last = without_suffix(last);
        }
        if parts.first().is_some_and(|first| roots.contains(first)) {
            if parts.last() == Some(&"__init__") {
                parts.pop();
            }
            return Ok(Some(parts.join(".")));
        }
        return Ok(None);
    }
    Ok(None)
}

/// `PurePath.with_suffix("")` for one name.
fn without_suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(index) if index > 0 && index < name.len() - 1 => &name[..index],
        _ => name,
    }
}

struct Debt {
    in_debt: usize,
    findings: Vec<Finding>,
}

#[allow(clippy::too_many_arguments)]
fn match_debt(
    loaded: &LoadedConfig,
    policy: &Policy,
    catalog: &Catalog,
    resolved: &[Resolved],
    sources: &HashMap<String, String>,
    active: &HashMap<&str, Severity>,
    args: &Args,
    scoped: bool,
    findings: Vec<Finding>,
    lines: &mut Lines,
) -> Option<Debt> {
    let config = &loaded.config;
    let debt_path = config["debt"].as_str().filter(|path| !path.is_empty());
    let Some(debt_path) = debt_path.filter(|_| !args.no_debt) else {
        return Some(Debt {
            in_debt: 0,
            findings,
        });
    };
    let root = loaded.path.parent()?;
    let file = root.join(debt_path);
    let entries = if vfs::is_file(&file) {
        let bytes = vfs::read(&file).ok()?;
        debt::parse(std::str::from_utf8(&bytes).ok()?).ok()?
    } else {
        Vec::new()
    };
    let selected = args
        .select
        .as_deref()
        .is_some_and(|select| !selectors(Some(select)).is_empty());
    let mut kept = Vec::new();
    for entry in entries {
        if !active.contains_key(entry.code.as_str()) {
            continue;
        }
        let rule = catalog.by_code(&entry.code)?;
        let module = match entry.path.as_deref() {
            Some(path) if !path.is_empty() => module_for(config, sources, path).ok()?,
            _ => None,
        };
        if policy
            .severity(rule, resolved, module.as_deref(), selected)
            .is_some()
        {
            kept.push(entry);
        }
    }
    let matched =
        debt::match_findings(kept, findings, &mut |finding| lines.snippet(finding)).ok()?;
    let mut findings = matched.new;
    if let Some(severity) = active.get(RESOLVED_DEBT).filter(|_| !scoped) {
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
            path: Some(debt_path.to_owned()),
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
            member: None,
            fix: None,
        }));
    }
    Some(Debt {
        in_debt: matched.known,
        findings,
    })
}

fn trace(reason: &str) {
    if env::var_os("SMELT_TRACE").is_some() {
        eprintln!("smelt trace: the Python CLI checks: {reason}");
    }
}

/// The report text and exit code, or why the Python CLI runs instead.
fn run(args: &Args) -> Result<(String, i32), &'static str> {
    let cwd = env::current_dir().map_err(|_| "no working directory")?;
    let config_path = locate(
        &cwd,
        args.config.as_deref().filter(|config| !config.is_empty()),
    )
    .ok_or("no configuration file")?;
    let loaded = profile::time("load configuration", || load_config(&config_path))
        .map_err(|_| "a configuration error")?;
    if loaded.member.is_some() || !validate::supported(&loaded.config) {
        return Err("a workspace or a policy only the Python engine resolves");
    }
    let catalog = Catalog::load(CATALOG).ok_or("an unreadable rule catalog")?;
    let root = loaded.path.parent().ok_or("no project root")?.to_path_buf();
    let scope =
        scope(&loaded, &root, &cwd, &args.paths).ok_or("a check path outside the project")?;
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
    // The Python CLI would read installed packages where its interpreter imports them.
    let search_path = if active
        .iter()
        .any(|(rule, _)| rule.code == INSTALLED_PACKAGES)
    {
        let python = crate::python().map_err(|_| "no Python interpreter")?;
        Some(
            interpreter_search_path(&python)
                .ok_or("an interpreter outside a virtual environment")?,
        )
    } else {
        None
    };
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
        scope.is_empty(),
        search_path,
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
    validate::missing_paths(&loaded.config, &root, &inventory, &resolved)
        .map_err(|()| "a configured path that matches nothing")?;
    policy
        .validate_packages(&resolved)
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
    let debt = match_debt(
        &loaded,
        &policy,
        &catalog,
        &resolved,
        &inventory.sources,
        &severities,
        args,
        !scope.is_empty(),
        findings,
        &mut lines,
    )
    .ok_or("a debt file the Python engine reports")?;
    let in_scope = |finding: &Finding| {
        scope.is_empty() || validate::under_any(finding.path.as_deref(), &scope)
    };
    let mut violations = gaps;
    violations.extend(debt.findings.into_iter().filter(in_scope));
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
        coverage,
        project: coverage::project_metadata(&loaded.path),
        diagnostics,
    };
    let text = match args.format() {
        Format::Json => report::render_json(&report, args.show_coverage),
        Format::Sarif => report::render_sarif(&report, env!("CARGO_PKG_VERSION")),
        Format::Github => report::render_github(&report),
        Format::Concise => text::render(
            &report,
            &mut |path, line| lines.line(path, line),
            false,
            true,
            args.show_hints,
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
                args.show_hints,
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
