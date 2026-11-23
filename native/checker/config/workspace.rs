//! uv workspace members and the member configs that refine the root policy.
use super::discovery::packages_in;
use super::errors::Issue;
use super::models::{POLICY_FIELDS, RULE_LIMITS, default_contract, join, normpath};
use super::paths;
use super::schema::{Out, string_list};
use super::yaml::Yaml;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::io;
use std::path::{Path, PathBuf};

const MEMBER_CONFIG_NAMES: [&str; 2] = ["smelt.yml", "smelt.yaml"];
const UNSUPPORTED_MEMBER_KEYS: [(&str, &str); 4] = [
    (
        "extends",
        "a member config inherits the workspace root implicitly",
    ),
    ("tests", "test options are configured in the workspace root"),
    (
        "suppressions",
        "the suppression policy is configured in the workspace root",
    ),
    ("members", "members are declared in pyproject.toml"),
];

/// A directory a `tool.uv.workspace.members` glob matched, or why none did.
pub struct MemberDirectory {
    pub path: String,
    pub skipped: Option<String>,
}

type Packages = Vec<(String, Vec<String>)>;

fn packages_of<'a>(packages: &'a Packages, member: &str) -> &'a [String] {
    packages
        .iter()
        .find(|(path, _)| path == member)
        .map_or(&[], |(_, names)| names.as_slice())
}

/// The `smelt.yml` of every declared member that has one, by member path.
pub fn member_config_files(root: &Path) -> Vec<(String, PathBuf)> {
    let mut found = Vec::new();
    for member in member_directories(root, false).unwrap_or_default() {
        if member.skipped.is_some() {
            continue;
        }
        let file = MEMBER_CONFIG_NAMES
            .iter()
            .map(|name| root.join(&member.path).join(name))
            .find(|candidate| candidate.is_file());
        if let Some(file) = file {
            found.push((member.path, file));
        }
    }
    found
}

/// Keys a member file may not set; the model checks the rest.
pub fn member_raw_issues(raw: &Yaml) -> Vec<Issue> {
    let Some(pairs) = raw.as_map() else {
        return vec![Issue::new("", "the top level must be a mapping")];
    };
    let has = |key: &str| pairs.iter().any(|(name, _)| name.as_str() == Some(key));
    let mut issues: Vec<Issue> = UNSUPPORTED_MEMBER_KEYS
        .iter()
        .filter(|(key, _)| has(key))
        .map(|(key, reason)| {
            Issue::new(
                *key,
                format!("not allowed in a workspace member config: {reason}"),
            )
        })
        .collect();
    if let Some(project) = raw.get("project").and_then(Yaml::as_map) {
        for (key, _) in project {
            if key.as_str() != Some("exclude") {
                let name = key.as_str().map_or_else(|| key.repr(), str::to_owned);
                issues.push(Issue::new(
                    format!("project.{name}"),
                    "not allowed in a workspace member config: the workspace root selects roots and packages; a member may only add `exclude`",
                ));
            }
        }
    }
    let root_only =
        "not allowed in a workspace member config: this policy is set once in the workspace root";
    if let Some(architecture) = raw
        .get("architecture")
        .filter(|value| value.as_map().is_some())
    {
        for key in ["import_graph", "members", "canonical_exports"] {
            if architecture.get(key).is_some() {
                issues.push(Issue::new(format!("architecture.{key}"), root_only));
            }
        }
    }
    if let Some(style) = raw.get("style").filter(|value| value.as_map().is_some()) {
        for key in ["forbidden_names", "file_naming"] {
            if style.get(key).is_some() {
                issues.push(Issue::new(format!("style.{key}"), root_only));
            }
        }
    }
    issues
}

/// Every member `tool.uv.workspace` declares, sorted by path.
pub fn member_directories(root: &Path, strict: bool) -> Result<Vec<MemberDirectory>, String> {
    let data = pyproject(root, strict)?;
    let mut workspace = &data;
    for key in ["tool", "uv", "workspace"] {
        match workspace.get(key) {
            None => return Ok(Vec::new()),
            Some(toml::Value::Table(table)) => workspace = table,
            Some(_) if strict => {
                return Err(format!(
                    "pyproject.toml: {key} must be a table for workspace discovery"
                ));
            }
            Some(_) => return Ok(Vec::new()),
        }
    }
    let patterns = toml_strings(
        workspace.get("members"),
        strict,
        "tool.uv.workspace.members",
    )?;
    let excluded = toml_strings(
        workspace.get("exclude"),
        strict,
        "tool.uv.workspace.exclude",
    )?;
    let resolved_root = paths::resolve(root);
    let mut found: BTreeMap<String, Option<String>> = BTreeMap::new();
    for pattern in patterns {
        let matches: Vec<PathBuf> = paths::glob(root, &pattern)
            .into_iter()
            .filter(|candidate| {
                let resolved = paths::resolve(candidate);
                candidate.is_dir()
                    && resolved.starts_with(&resolved_root)
                    && resolved != resolved_root
            })
            .collect();
        if matches.is_empty() {
            found
                .entry(pattern.clone())
                .or_insert_with(|| Some("matches no directory".to_owned()));
        }
        for candidate in matches {
            let relative = paths::posix(candidate.strip_prefix(root).unwrap_or(&candidate));
            let is_excluded = excluded.iter().any(|entry| {
                paths::glob(root, entry)
                    .iter()
                    .any(|other| paths::same_path(other, &candidate))
            });
            let skipped = is_excluded.then(|| "excluded by tool.uv.workspace.exclude".to_owned());
            found.insert(relative, skipped);
        }
    }
    Ok(found
        .into_iter()
        .map(|(path, skipped)| MemberDirectory { path, skipped })
        .collect())
}

fn pyproject(directory: &Path, strict: bool) -> Result<toml::Table, String> {
    let failure = |detail: String| {
        if strict {
            Err(format!(
                "cannot discover workspace from pyproject.toml: {detail}"
            ))
        } else {
            Ok(toml::Table::new())
        }
    };
    let text = match paths::read_text(&directory.join("pyproject.toml")) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Ok(toml::Table::new());
        }
        Err(error) => return failure(error.to_string()),
    };
    match text.parse::<toml::Table>() {
        Ok(table) => Ok(table),
        Err(error) => failure(error.to_string()),
    }
}

fn toml_strings(
    value: Option<&toml::Value>,
    strict: bool,
    field: &str,
) -> Result<Vec<String>, String> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    let items = value.as_array();
    if strict && !items.is_some_and(|items| items.iter().all(toml::Value::is_str)) {
        return Err(format!("pyproject.toml: {field} must be a list of strings"));
    }
    Ok(items
        .into_iter()
        .flatten()
        .filter_map(toml::Value::as_str)
        .map(str::to_owned)
        .collect())
}

fn declares_project(root: &Path) -> bool {
    pyproject(root, false)
        .ok()
        .is_some_and(|data| data.get("project").is_some_and(toml::Value::is_table))
}

/// Apply the roots inside every member, then compile the member configs.
pub fn expand_workspace(config: &mut Value, root: &Path) -> Result<(), Vec<Issue>> {
    let members: Vec<String> = member_directories(root, true)
        .map_err(|message| vec![Issue::new("project.workspace", message)])?
        .into_iter()
        .filter(|member| member.skipped.is_none())
        .map(|member| member.path)
        .collect();
    let declared: BTreeSet<&String> = config["members"]
        .as_object()
        .map(|entries| entries.keys().collect())
        .unwrap_or_default();
    let unknown: Vec<Issue> = declared
        .iter()
        .filter(|path| !members.contains(path))
        .map(|path| {
            Issue::new(
                format!("members.{path}"),
                "not a declared uv workspace member",
            )
        })
        .collect();
    if !unknown.is_empty() {
        return Err(unknown);
    }
    let selected = string_list(config["project"].get("root_packages"));
    let configured_sources = string_list(config["project"].get("source_roots"));
    let configured_tests = string_list(config["project"].get("test_roots"));
    let mut bases: Vec<String> = if declares_project(root) {
        vec![String::new()]
    } else {
        Vec::new()
    };
    bases.extend(members.iter().cloned());
    let sets_sources = string_list(config["project"].get("__set__"))
        .iter()
        .any(|key| key == "source_roots");
    let sources: Vec<String> = if sets_sources {
        existing(root, &bases, &configured_sources)
    } else {
        bases
            .iter()
            .map(|base| default_root(root, base, &selected))
            .collect()
    };
    if sources.is_empty() {
        return Err(vec![Issue::new(
            "project.source_roots",
            format!(
                "no workspace member contains a source root ({})",
                configured_sources.join(", ")
            ),
        )]);
    }
    let mut test_bases = vec![String::new()];
    test_bases.extend(members.iter().cloned());
    let tests = existing(root, &test_bases, &configured_tests);
    let packages = member_packages(root, &members, &sources, &selected)?;
    let root_packages = if selected.is_empty() {
        let mut names: Vec<String> = Vec::new();
        for name in packages.iter().flat_map(|(_, names)| names) {
            if !names.contains(name) {
                names.push(name.clone());
            }
        }
        names
    } else {
        selected
    };
    config["project"]["source_roots"] = json!(sources);
    config["project"]["test_roots"] = json!(tests);
    config["project"]["root_packages"] = json!(root_packages);
    let mut scoped = Out::new();
    scoped.insert(String::new(), json!(packages_of(&packages, "")));
    for path in &members {
        scoped.insert(path.clone(), json!(packages_of(&packages, path)));
    }
    config["__workspace_packages__"] = Value::Object(scoped);
    let mut base = config.clone();
    base["members"] = json!({});
    compile_members(config, &packages)?;
    config["__workspace_base__"] = base;
    Ok(())
}

fn default_root(root: &Path, base: &str, selected: &[String]) -> String {
    let src = if base.is_empty() {
        "src".to_owned()
    } else {
        join(base, "src")
    };
    let directory = root.join(&src);
    if !packages_in(&directory).is_empty()
        || (!selected.is_empty() && !package_directories(&directory, selected).is_empty())
    {
        return src;
    }
    if base.is_empty() {
        ".".to_owned()
    } else {
        base.to_owned()
    }
}

fn existing(root: &Path, bases: &[String], roots: &[String]) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    for base in bases {
        for entry in roots {
            let path = normpath(&join(base, entry));
            if root.join(&path).is_dir() && !found.contains(&path) {
                found.push(path);
            }
        }
    }
    found
}

fn package_directories(source: &Path, selected: &[String]) -> Vec<PathBuf> {
    if selected.is_empty() {
        return packages_in(source);
    }
    selected
        .iter()
        .map(|name| source.join(name))
        .filter(|path| path.is_dir())
        .collect()
}

fn owner<'a>(path: &str, members: &'a [String]) -> &'a str {
    members
        .iter()
        .filter(|member| {
            path == member.as_str()
                || path
                    .strip_prefix(member.as_str())
                    .is_some_and(|rest| rest.starts_with('/'))
        })
        .max_by_key(|member| member.len())
        .map_or("", String::as_str)
}

fn directory_name(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// Top-level package names per member; `""` holds the root's own packages.
fn member_packages(
    root: &Path,
    members: &[String],
    sources: &[String],
    selected: &[String],
) -> Result<Packages, Vec<Issue>> {
    let member_dirs: Vec<PathBuf> = members
        .iter()
        .map(|path| paths::resolve(&root.join(path)))
        .collect();
    let mut seen_sources: Vec<&String> = Vec::new();
    let mut candidates: Vec<(&String, Vec<PathBuf>)> = Vec::new();
    for source in sources {
        if !seen_sources.contains(&source) {
            seen_sources.push(source);
            candidates.push((source, package_directories(&root.join(source), selected)));
        }
    }
    let regular: Vec<&PathBuf> = candidates
        .iter()
        .flat_map(|(_, directories)| directories)
        .filter(|package| !selected.is_empty() && package.join("__init__.py").is_file())
        .collect();
    let regular_names: BTreeSet<String> = regular.iter().map(|path| directory_name(path)).collect();
    let mut owners: BTreeMap<String, String> = BTreeMap::new();
    let mut packages: Packages = Vec::new();
    for (source, directories) in &candidates {
        let member = owner(source, members);
        for package in directories {
            let name = directory_name(package);
            if regular_names.contains(&name) && !regular.contains(&package) {
                continue;
            }
            let resolved = paths::resolve(package);
            if member.is_empty()
                && member_dirs
                    .iter()
                    .any(|directory| resolved.starts_with(directory))
            {
                continue;
            }
            let previous = owners
                .entry(name.clone())
                .or_insert_with(|| member.to_owned());
            if previous != member {
                let shown = |path: &str| {
                    if path.is_empty() {
                        ".".to_owned()
                    } else {
                        path.to_owned()
                    }
                };
                return Err(vec![Issue::new(
                    "project.workspace",
                    format!(
                        "package \"{name}\" exists in workspace members {} and {}; top-level package names must be unique",
                        shown(previous),
                        shown(member)
                    ),
                )]);
            }
            let slot = match packages.iter().position(|(path, _)| path == member) {
                Some(slot) => slot,
                None => {
                    packages.push((member.to_owned(), Vec::new()));
                    packages.len() - 1
                }
            };
            if !packages[slot].1.contains(&name) {
                packages[slot].1.push(name);
            }
        }
    }
    Ok(packages)
}

fn first_segment(pattern: &str) -> &str {
    pattern.split('.').next().unwrap_or(pattern)
}

fn object(value: &Value) -> Out {
    value.as_object().cloned().unwrap_or_default()
}

/// Turn member structure, ignores and exclusions into root-level equivalents.
fn compile_members(config: &mut Value, packages: &Packages) -> Result<(), Vec<Issue>> {
    let mut contracts = object(&config["__structure__"]["contracts"]);
    let mut ignore = config["ignore"].as_array().cloned().unwrap_or_default();
    let mut exclude = config["project"]["exclude"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let mut sorted: Vec<&(String, Vec<String>)> = packages.iter().collect();
    sorted.sort_by(|left, right| left.0.cmp(&right.0));
    let mut issues = Vec::new();
    for (section, entries) in [
        ("architecture.packages", &config["architecture"]["packages"]),
        ("style.overrides", &config["style"]["overrides"]),
    ] {
        for (name, policy) in entries.as_object().into_iter().flatten() {
            let pattern = policy["match"].as_str().unwrap_or_default();
            let first = first_segment(pattern);
            let owners: Vec<&str> = sorted
                .iter()
                .filter(|(path, names)| {
                    !path.is_empty()
                        && !names.is_empty()
                        && (first == "*" || names.iter().any(|item| item == first))
                })
                .map(|(path, _)| path.as_str())
                .collect();
            if !owners.is_empty() {
                issues.push(Issue::new(
                    format!("{section}.{name}.match"),
                    format!(
                        "\"{pattern}\" can match packages in {}; move the entry to the owning member config",
                        owners.join(", ")
                    ),
                ));
            }
        }
    }
    let mut compiled_members = Out::new();
    for (path, member) in object(&config["members"]) {
        let names = packages_of(packages, &path);
        let location = format!("members.{path}");
        let structure = &member["__structure__"];
        let options = policy_options(structure);
        let reasons = object(&structure["reasons"]);
        issues.extend(style_reason_issues(
            &options,
            &reasons,
            &format!("{location}.style"),
            &config["style"],
        ));
        if !options.is_empty() {
            for package in names {
                let name = if names.len() == 1 {
                    path.clone()
                } else {
                    format!("{path}:{package}")
                };
                let mut contract = default_contract();
                contract.insert("match".to_owned(), json!(package));
                contract.insert("reasons".to_owned(), Value::Object(reasons.clone()));
                for (option, value) in &options {
                    contract.insert(option.clone(), value.clone());
                }
                contract.insert(
                    "__style_location__".to_owned(),
                    json!(format!("{location}.style")),
                );
                contract.insert("__style_override__".to_owned(), json!(false));
                contracts.insert(name, Value::Object(contract));
            }
        }
        let mut inherited = config["__structure__"].clone();
        for (option, value) in &options {
            inherited[option] = value.clone();
        }
        let overrides = object(&member["style"]["overrides"]);
        for (name, contract) in object(&structure["contracts"]) {
            let qualified = format!("{path}:{name}");
            let policy = if overrides.contains_key(&name) {
                "style.overrides"
            } else {
                "architecture.packages"
            };
            let contract_location = format!("{location}.{policy}.{name}");
            let pattern = contract["match"].as_str().unwrap_or_default();
            if !names.iter().any(|item| item == first_segment(pattern)) {
                let shown = if names.is_empty() {
                    "none".to_owned()
                } else {
                    names.join(", ")
                };
                issues.push(Issue::new(
                    format!("{contract_location}.match"),
                    format!("\"{pattern}\" is outside this member; its packages are {shown}"),
                ));
            }
            if contracts.contains_key(&qualified) {
                issues.push(Issue::new(
                    contract_location.clone(),
                    format!("contract name \"{qualified}\" is already used"),
                ));
            }
            issues.extend(style_reason_issues(
                &policy_options(&contract),
                &object(&contract["reasons"]),
                &contract_location,
                &inherited,
            ));
            let mut compiled = contract;
            if compiled["__style_override__"] == json!(true) {
                compiled["__style_location__"] = json!(contract_location);
            }
            contracts.insert(qualified, compiled);
        }
        for entry in member["ignore"].as_array().into_iter().flatten() {
            let mut entry = entry.clone();
            let moved: Vec<String> = string_list(entry.get("paths"))
                .iter()
                .map(|item| join(&path, item))
                .collect();
            entry["paths"] = json!(moved);
            ignore.push(entry);
        }
        for entry in member["project"]["exclude"]
            .as_array()
            .into_iter()
            .flatten()
        {
            let mut entry = entry.clone();
            if let Some(text) = entry.as_str() {
                entry = json!(join(&path, text));
            } else if let Some(text) = entry["path"].as_str() {
                let moved = join(&path, text);
                entry["path"] = json!(moved);
            }
            exclude.push(entry);
        }
        let mut located = member.clone();
        located["__packages__"] = json!(names);
        compiled_members.insert(path, located);
    }
    if !issues.is_empty() {
        return Err(issues);
    }
    config["ignore"] = Value::Array(ignore);
    config["project"]["exclude"] = Value::Array(exclude);
    config["members"] = Value::Object(compiled_members);
    config["__structure__"]["contracts"] = Value::Object(contracts);
    Ok(())
}

fn policy_options(policy: &Value) -> Vec<(String, Value)> {
    POLICY_FIELDS
        .iter()
        .chain(&RULE_LIMITS)
        .filter_map(|name| {
            policy
                .get(*name)
                .filter(|value| !value.is_null())
                .map(|value| ((*name).to_owned(), value.clone()))
        })
        .collect()
}

fn style_reason_issues(
    options: &[(String, Value)],
    reasons: &Out,
    location: &str,
    inherited: &Value,
) -> Vec<Issue> {
    options
        .iter()
        .filter(|(name, value)| {
            POLICY_FIELDS.contains(&name.as_str())
                && !reasons.contains_key(name)
                && inherited
                    .get(name)
                    .is_some_and(|other| !other.is_null() && other != value)
        })
        .map(|(name, _)| {
            Issue::new(
                format!("{location}.{name}"),
                "changing the inherited value requires a reason; use {value: ..., reason: ...}",
            )
        })
        .collect()
}
