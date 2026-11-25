//! Load a project's configuration, as in `smelt.config.loader`.
use super::compose::{Composition, add_member, compose_config};
use super::discovery::packages_in;
use super::errors::{ConfigError, Issue};
use super::models::{SMELT, UNCOVERED_SOURCE, rules_layer_ty};
use super::paths;
use super::schema::{Out, Validator, string_list};
use super::semantics::{partial_layer_issues, validate_semantics};
use super::workspace::{expand_workspace, member_config_files, member_directories};
use super::yaml::Yaml;
use crate::checker::vfs;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

const CONFIG_FILENAMES: [&str; 2] = ["smelt.yml", "smelt.yaml"];
const VERSION_1: &str = "version 1 is no longer read; run `smelt config migrate --write` to convert this file to version 2";

/// A validated configuration and where its values came from.
pub struct LoadedConfig {
    pub config: Value,
    pub path: PathBuf,
    pub warnings: Vec<Issue>,
    pub inputs: Vec<PathBuf>,
    pub origins: BTreeMap<String, String>,
    pub member: Option<String>,
    pub rule_sources: Vec<String>,
}

/// Load a project config; a workspace member's config selects its root.
pub fn load_config(path: &Path) -> Result<LoadedConfig, ConfigError> {
    let mut path = paths::resolve(path);
    let mut member = None;
    let selected = workspace_of(&path).and_then(|workspace| {
        if let Some((root, owner)) = workspace {
            path = root;
            member = owner;
        }
        load_selected(&path, member.clone())
    });
    let parent = path.parent().unwrap_or(&path).to_path_buf();
    selected.map_err(|error| error.relative_to(&parent))
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn config_in(directory: &Path) -> Option<PathBuf> {
    CONFIG_FILENAMES
        .iter()
        .map(|name| directory.join(name))
        .find(|candidate| vfs::is_file(candidate))
}

/// Find the enclosing workspace even when a nested policy is misplaced.
fn workspace_of(path: &Path) -> Result<Option<(PathBuf, Option<String>)>, ConfigError> {
    if !CONFIG_FILENAMES.contains(&file_name(path).as_str()) {
        return Ok(None);
    }
    let Some(directory) = path.parent() else {
        return Ok(None);
    };
    for parent in directory.ancestors().skip(1) {
        let Some(candidate) = config_in(parent) else {
            continue;
        };
        let composed = compose_config(&candidate)
            .map_err(|error| error.relative_to(candidate.parent().unwrap_or(parent)))?;
        let members: Vec<String> = member_directories(parent, false)
            .unwrap_or_default()
            .into_iter()
            .filter(|member| member.skipped.is_none())
            .map(|member| member.path)
            .collect();
        let relative = paths::posix(directory.strip_prefix(parent).unwrap_or(directory));
        if composed.declares_workspace() {
            let owner = members
                .iter()
                .filter(|name| {
                    relative == **name
                        || relative
                            .strip_prefix(name.as_str())
                            .is_some_and(|rest| rest.starts_with('/'))
                })
                .max_by_key(|name| name.len())
                .cloned();
            return Ok(Some((candidate, owner)));
        }
        let declared = members.contains(&relative);
        if declared && !compose_config(path)?.has_key("version") {
            return Err(ConfigError::new(
                vec![Issue::new(
                    "project.workspace",
                    "this looks like a member config; set project.workspace: uv in the workspace root",
                )],
                Some(paths::posix(&candidate)),
            ));
        }
        if declared {
            return Ok(None);
        }
    }
    Ok(None)
}

fn load_selected(path: &Path, member: Option<String>) -> Result<LoadedConfig, ConfigError> {
    let mut composed = compose_config(path)?;
    let name = file_name(path);
    if composed.has_key("members") {
        return Err(ConfigError::new(
            vec![Issue::new(
                "members",
                "not a config key; each workspace member keeps its own smelt.yml",
            )],
            Some(
                composed
                    .source_for("members")
                    .unwrap_or_else(|| name.clone()),
            ),
        ));
    }
    let root = path.parent().unwrap_or(path).to_path_buf();
    let mut misplaced = Vec::new();
    if composed.declares_workspace() {
        for (member_path, file) in member_config_files(&root) {
            add_member(&mut composed, &member_path, &file)?;
        }
        misplaced = misplaced_configs(&root, &composed.files);
    }
    let parsed = parse_config(&composed.raw_value(), Some(&name), &root);
    let (mut config, warnings) = match parsed {
        Ok(parsed) => parsed,
        Err(error) if composed.composite() => return Err(with_origins(error, &composed)),
        Err(error) => return Err(error),
    };
    if composed.rule_layers.len() > 1 {
        let layers = validated_layers(&composed.rule_layers)
            .map_err(|issues| with_origins(ConfigError::new(issues, Some(name)), &composed))?;
        config["__rule_layers__"] = Value::Array(layers);
    }
    let mut shown: Vec<Issue> = if composed.composite() {
        warnings
            .into_iter()
            .map(|issue| Issue {
                source: composed.source_for(&issue.path),
                ..issue
            })
            .collect()
    } else {
        warnings
    };
    shown.extend(misplaced);
    Ok(LoadedConfig {
        config,
        path: path.to_path_buf(),
        warnings: shown,
        inputs: composed.files,
        origins: composed.origins,
        member,
        rule_sources: composed.rule_sources,
    })
}

fn validated_layers(layers: &[Yaml]) -> Result<Vec<Value>, Vec<Issue>> {
    let ty = rules_layer_ty();
    let mut validated = Vec::with_capacity(layers.len());
    for layer in layers {
        let mut validator = Validator::at_key("rules");
        match validator.check(&ty, layer) {
            Some(value) if validator.issues.is_empty() => validated.push(value),
            _ => return Err(validator.issues),
        }
    }
    Ok(validated)
}

fn with_origins(error: ConfigError, composed: &Composition) -> ConfigError {
    let issues = error
        .issues
        .into_iter()
        .map(|issue| Issue {
            source: composed.source_for(&issue.path),
            path: composed.member_relative(&issue.path),
            ..issue
        })
        .collect();
    ConfigError::new(issues, error.source)
}

fn misplaced_configs(root: &Path, known: &[PathBuf]) -> Vec<Issue> {
    let mut found = Vec::new();
    collect_configs(root, &mut found);
    found.sort();
    found
        .into_iter()
        .filter(|candidate| !known.iter().any(|path| paths::same_path(path, candidate)))
        .map(|candidate| {
            let relative = candidate.strip_prefix(root).unwrap_or(&candidate);
            Issue {
                source: Some(paths::posix(relative)),
                ..Issue::new(
                    "",
                    "ignored config; workspace policy belongs at the root or in a declared member directory",
                )
            }
        })
        .collect()
}

fn collect_configs(directory: &Path, found: &mut Vec<PathBuf>) {
    const SKIPPED: [&str; 6] = [
        "venv",
        "env",
        "node_modules",
        "build",
        "dist",
        "__pycache__",
    ];
    for entry in paths::children(directory) {
        let name = file_name(&entry);
        if CONFIG_FILENAMES.contains(&name.as_str()) && vfs::is_file(&entry) {
            found.push(entry);
        } else if !name.starts_with('.')
            && !SKIPPED.contains(&name.as_str())
            && vfs::is_dir(&entry)
            && paths::same_path(&paths::resolve(&entry), &entry)
        {
            collect_configs(&entry, found);
        }
    }
}

fn outdated_versions(raw: &Yaml) -> Vec<Issue> {
    let is_one = |file: &Yaml| {
        matches!(file.get("version"), Some(Yaml::Int(1) | Yaml::Bool(true)))
            || matches!(file.get("version"), Some(Yaml::Float(value)) if *value == 1.0)
    };
    let mut issues = Vec::new();
    if is_one(raw) {
        issues.push(Issue::new("version", VERSION_1));
    }
    for (name, member) in raw
        .get("members")
        .and_then(Yaml::as_map)
        .into_iter()
        .flatten()
    {
        if member.as_map().is_some() && is_one(member) {
            let name = name.as_str().map_or_else(|| name.repr(), str::to_owned);
            issues.push(Issue::new(format!("members.{name}.version"), VERSION_1));
        }
    }
    issues
}

/// Validate a raw config; `root` lets omitted root_packages be discovered.
fn parse_config(
    raw: &Yaml,
    source: Option<&str>,
    root: &Path,
) -> Result<(Value, Vec<Issue>), ConfigError> {
    let owned = source.map(str::to_owned);
    let fail = |issues: Vec<Issue>| ConfigError::new(issues, owned.clone());
    if raw.as_map().is_none() {
        return Err(fail(vec![Issue::new(
            "",
            "the top level must be a mapping",
        )]));
    }
    if raw.get("version").is_none() {
        return Err(fail(vec![Issue::new(
            "version",
            "`version: 2` is required",
        )]));
    }
    let outdated = outdated_versions(raw);
    if !outdated.is_empty() {
        return Err(fail(outdated));
    }
    let mut validator = Validator::default();
    let validated = validator.model(&SMELT, raw);
    let Some(mut config) = validated.filter(|_| validator.issues.is_empty()) else {
        let mut issues = validator.issues;
        let mut reported: BTreeSet<String> =
            issues.iter().map(|issue| issue.path.clone()).collect();
        for issue in partial_layer_issues(raw) {
            if reported.insert(issue.path.clone()) {
                issues.push(Issue {
                    semantic: true,
                    ..issue
                });
            }
        }
        return Err(fail(issues));
    };
    let has_members = config["members"]
        .as_object()
        .is_some_and(|members| !members.is_empty());
    if has_members && config["project"]["workspace"].is_null() {
        return Err(fail(vec![Issue::new(
            "members",
            "member configs require project.workspace: uv",
        )]));
    }
    if !config["project"]["workspace"].is_null() {
        expand_workspace(&mut config, root).map_err(&fail)?;
    }
    with_root_packages(&mut config, root).map_err(&fail)?;
    let (errors, warnings) = validate_semantics(&config);
    if !errors.is_empty() {
        return Err(fail(errors));
    }
    Ok((config, warnings))
}

fn with_root_packages(config: &mut Value, root: &Path) -> Result<(), Vec<Issue>> {
    if !string_list(config["project"].get("root_packages")).is_empty() {
        return Ok(());
    }
    let source_roots = string_list(config["project"].get("source_roots"));
    let mut found: Vec<String> = Vec::new();
    for source_root in &source_roots {
        for package in packages_in(&root.join(source_root)) {
            let name = file_name(&package);
            if !found.contains(&name) {
                found.push(name);
            }
        }
    }
    if found.is_empty() {
        return Err(vec![Issue::new(
            "project.root_packages",
            format!(
                "no packages found under source_roots ({}); set root_packages or source_roots",
                source_roots.join(", ")
            ),
        )]);
    }
    config["project"]["root_packages"] = json!(found);
    Ok(())
}

/// Whether the root rules enable architecture/uncovered-source.
pub fn strict_coverage(config: &Value) -> bool {
    let mut setting = None;
    for layer in rule_layers(config) {
        for key in UNCOVERED_SOURCE {
            if let Some(value) = layer.get(key) {
                setting = Some(value.clone());
            }
        }
    }
    let severity = match &setting {
        Some(Value::Object(entry)) => entry.get("severity"),
        other => other.as_ref(),
    };
    severity.is_some_and(|value| !value.is_null() && value != "off")
}

pub fn rule_layers(config: &Value) -> Vec<&Value> {
    match config.get("__rule_layers__").and_then(Value::as_array) {
        Some(layers) => layers.iter().collect(),
        None => vec![&config["rules"]],
    }
}

fn inline_reasons(policy: &mut Value) {
    let Some(entries) = policy.as_object_mut() else {
        return;
    };
    let reasons = entries.shift_remove("reasons");
    for (name, reason) in reasons.iter().filter_map(Value::as_object).flatten() {
        if let Some(value) = entries.get_mut(name) {
            *value = json!({"value": value.take(), "reason": reason});
        }
    }
}

fn dump_style(style: &mut Value) {
    if let Some(overrides) = style.get_mut("overrides").and_then(Value::as_object_mut) {
        overrides.values_mut().for_each(inline_reasons);
    }
    inline_reasons(style);
}

fn dump_architecture(architecture: &mut Value) {
    if let Some(entries) = architecture.as_object_mut() {
        entries.shift_remove("__set__");
    }
}

fn dump_structure(structure: &Value) -> Value {
    let mut dumped = structure.clone();
    if let Some(contracts) = dumped.get_mut("contracts").and_then(Value::as_object_mut) {
        for contract in contracts.values_mut() {
            if let Some(entries) = contract.as_object_mut() {
                entries.shift_remove("__style_location__");
                entries.shift_remove("__style_override__");
            }
            inline_reasons(contract);
        }
    }
    inline_reasons(&mut dumped);
    dumped
}

fn dump_model(config: &Value) -> Value {
    let mut dumped = config.clone();
    if let Some(entries) = dumped.as_object_mut() {
        for hidden in [
            "__structure__",
            "__workspace_packages__",
            "__rule_layers__",
            "__workspace_base__",
        ] {
            entries.shift_remove(hidden);
        }
    }
    if let Some(project) = dumped["project"].as_object_mut() {
        project.shift_remove("__set__");
    }
    dump_architecture(&mut dumped["architecture"]);
    dump_style(&mut dumped["style"]);
    if let Some(members) = dumped["members"].as_object_mut() {
        for member in members.values_mut() {
            if let Some(entries) = member.as_object_mut() {
                entries.shift_remove("__structure__");
                entries.shift_remove("__packages__");
            }
            dump_architecture(&mut member["architecture"]);
            dump_style(&mut member["style"]);
        }
    }
    dumped
}

fn contract_metadata(structure: &Value) -> Value {
    let mut metadata = Out::new();
    for (name, contract) in structure["contracts"].as_object().into_iter().flatten() {
        metadata.insert(
            name.clone(),
            json!({
                "style_location": contract.get("__style_location__").cloned().unwrap_or(Value::Null),
                "style_override": contract.get("__style_override__").cloned().unwrap_or(json!(false)),
            }),
        );
    }
    Value::Object(metadata)
}

fn issue_json(issue: &Issue) -> Value {
    json!({
        "path": issue.path,
        "message": issue.message,
        "source": issue.source,
    })
}

/// The whole loaded configuration, for comparison with the Python loader.
pub fn describe(loaded: &LoadedConfig) -> Value {
    let config = &loaded.config;
    let mut members = Out::new();
    for (path, member) in config["members"].as_object().into_iter().flatten() {
        members.insert(
            path.clone(),
            json!({
                "structure": dump_structure(&member["__structure__"]),
                "contracts": contract_metadata(&member["__structure__"]),
                "packages": member.get("__packages__").cloned().unwrap_or(json!([])),
            }),
        );
    }
    let base = config.get("__workspace_base__").map(|base| {
        json!({
            "config": dump_model(base),
            "structure": dump_structure(&base["__structure__"]),
        })
    });
    json!({
        "ok": true,
        "config": dump_model(config),
        "structure": dump_structure(&config["__structure__"]),
        "contracts": contract_metadata(&config["__structure__"]),
        "members": members,
        "workspace_base": base,
        "workspace_packages": config.get("__workspace_packages__").cloned().unwrap_or(json!({})),
        "rule_layers": rule_layers(config),
        "strict_coverage": strict_coverage(config),
        "warnings": loaded.warnings.iter().map(issue_json).collect::<Vec<_>>(),
        "path": paths::posix(&loaded.path),
        "inputs": loaded.inputs.iter().map(|path| paths::posix(path)).collect::<Vec<_>>(),
        "origins": loaded.origins,
        "member": loaded.member,
        "rule_sources": loaded.rule_sources,
    })
}
