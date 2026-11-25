//! The checks after the analysis that make the Python CLI exit with a
//! configuration or option error. Each returns `Err(())` where Python would
//! raise, so the Python CLI runs again and reports it.
use super::policy::Resolved;
use crate::checker::config::paths;
use crate::checker::config::workspace::member_directories;
use crate::checker::patterns::{ModulePattern, PathPattern};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::path::Path;

/// The analyzed files, from the core's inventory.
pub struct Inventory {
    /// Each source path and its module.
    pub sources: HashMap<String, String>,
    pub modules: HashSet<String>,
    pub packages: HashSet<String>,
    /// Each module and the path of its source, else of its package.
    pub module_paths: HashMap<String, String>,
    /// Every source and test path.
    pub python_paths: Vec<String>,
    /// Every source, test and checked YAML path.
    pub check_paths: Vec<String>,
    missing_root_packages: HashSet<String>,
}

impl Inventory {
    pub fn new(files: &Value) -> Self {
        let text = |value: &Value, key: &str| value[key].as_str().unwrap_or_default().to_owned();
        let mut sources = HashMap::new();
        let mut check_paths = Vec::new();
        let mut modules = HashSet::new();
        for source in files["sources"].as_array().into_iter().flatten() {
            sources.insert(text(source, "path"), text(source, "module"));
            check_paths.push(text(source, "path"));
            modules.insert(text(source, "module"));
        }
        for test in files["tests"].as_array().into_iter().flatten() {
            check_paths.push(text(test, "path"));
        }
        let python_paths = check_paths.clone();
        for path in files["yaml_paths"].as_array().into_iter().flatten() {
            check_paths.push(path.as_str().unwrap_or_default().to_owned());
        }
        let packages: HashSet<String> = files["packages"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|package| text(package, "module"))
            .collect();
        modules.extend(packages.iter().cloned());
        let mut module_paths = HashMap::new();
        for package in files["packages"].as_array().into_iter().flatten() {
            module_paths
                .entry(text(package, "module"))
                .or_insert_with(|| text(package, "path"));
        }
        for (path, module) in &sources {
            module_paths.insert(module.clone(), path.clone());
        }
        Self {
            sources,
            modules,
            packages,
            module_paths,
            python_paths,
            check_paths,
            missing_root_packages: files["missing_root_packages"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect(),
        }
    }
}

fn texts(value: &Value) -> impl Iterator<Item = &str> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
}

/// `missing_paths`: config entries that point at nothing.
pub fn missing_paths(
    config: &Value,
    root: &Path,
    inventory: &Inventory,
    resolved: &[Resolved],
    features: &[(String, Vec<String>)],
) -> Result<(), ()> {
    let project = &config["project"];
    let is_dir = |directory: &str| crate::checker::vfs::is_dir(&root.join(directory));
    if !texts(&project["source_roots"]).all(is_dir) {
        return Err(());
    }
    if config["tests"]["layout"] == "mirror" && !texts(&project["test_roots"]).all(is_dir) {
        return Err(());
    }
    if texts(&project["root_packages"])
        .any(|package| inventory.missing_root_packages.contains(package))
    {
        return Err(());
    }
    let found = |scope: &str| {
        features
            .iter()
            .find(|(path, _)| path == scope)
            .map_or(&[][..], |(_, names)| names.as_slice())
    };
    // `_architecture_views`: the root architecture, then each member's.
    let mut views = vec![(&config["architecture"], found(""))];
    if let Some(packages) = config["__workspace_packages__"]
        .as_object()
        .filter(|packages| !packages.is_empty())
    {
        let mut members: Vec<&String> = packages.keys().filter(|path| !path.is_empty()).collect();
        members.sort();
        views.extend(members.into_iter().filter_map(|path| {
            let architecture = &config["members"][path]["architecture"];
            (!architecture.is_null()).then(|| (architecture, found(path)))
        }));
    }
    for (architecture, features) in views {
        missing_modules(architecture, inventory)?;
        unknown_allowed_features(architecture, features)?;
    }
    unmatched_member_globs(config, root)?;
    let contracts = config["__structure__"]["contracts"].as_object();
    // Two overrides of one package may set conflicting policies.
    let mut overridden: HashSet<&str> = HashSet::new();
    for contract in resolved {
        let spec = &contracts.and_then(|contracts| contracts.get(&contract.name));
        if spec.is_some_and(|spec| spec["__style_override__"] == true)
            && !overridden.insert(contract.package.as_str())
        {
            return Err(());
        }
    }
    let matched: HashSet<&str> = resolved
        .iter()
        .map(|contract| contract.name.as_str())
        .collect();
    if contracts
        .into_iter()
        .flatten()
        .any(|(name, _)| !matched.contains(name.as_str()))
    {
        return Err(());
    }
    Ok(())
}

/// `_missing_modules`: modules an architecture names that do not exist.
fn missing_modules(architecture: &Value, inventory: &Inventory) -> Result<(), ()> {
    let features = &architecture["features"];
    if !features.is_null() {
        let package = match features["root"].as_str() {
            Some(root) => root.to_owned(),
            None => {
                let pattern = features["pattern"].as_str().unwrap_or_default();
                pattern
                    .rsplit_once('.')
                    .map_or(pattern, |(head, _)| head)
                    .to_owned()
            }
        };
        if !inventory.packages.contains(&package) {
            return Err(());
        }
    }
    for key in ["shared", "composition_root"] {
        if !texts(&architecture[key]).all(|module| inventory.modules.contains(module)) {
            return Err(());
        }
    }
    for layer in architecture["layers"]
        .as_object()
        .into_iter()
        .flatten()
        .map(|(_, layer)| layer)
    {
        if !texts(&layer["modules"]).all(|module| inventory.modules.contains(module)) {
            return Err(());
        }
    }
    for pattern in texts(&architecture["wiring"]) {
        let pattern = ModulePattern::new(pattern).map_err(|_| ())?;
        if !inventory
            .modules
            .iter()
            .any(|module| pattern.matches(module))
        {
            return Err(());
        }
    }
    Ok(())
}

/// `_unknown_allowed_features`: a `{from, to}` allowance names a feature the
/// architecture does not have.
fn unknown_allowed_features(architecture: &Value, features: &[String]) -> Result<(), ()> {
    for allowance in architecture["cross_feature"]["allow"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|allowance| allowance.is_object())
    {
        for key in ["from", "to"] {
            let value = allowance[key].as_str().unwrap_or_default();
            let feature = value.split('.').next().unwrap_or_default();
            if !features.iter().any(|known| known == feature) {
                return Err(());
            }
        }
    }
    Ok(())
}

/// `_unmatched_member_globs`: member boundaries whose globs match no member.
fn unmatched_member_globs(config: &Value, root: &Path) -> Result<(), ()> {
    let boundaries = config["architecture"]["members"].as_object();
    if boundaries.is_none_or(serde_json::Map::is_empty) {
        return Ok(());
    }
    let mut members = Vec::new();
    if config["project"]["workspace"] == "uv" {
        members.push(".".to_owned());
        members.extend(
            member_directories(root, false)
                .map_err(|_| ())?
                .into_iter()
                .filter(|member| member.skipped.is_none())
                .map(|member| member.path),
        );
    }
    let matched = |pattern: &str| {
        PathPattern::new(pattern)
            .is_ok_and(|pattern| members.iter().any(|member| pattern.matches(member)))
    };
    for (pattern, policy) in boundaries.into_iter().flatten() {
        if !matched(pattern) || !texts(&policy["may_depend_on"]).all(matched) {
            return Err(());
        }
    }
    Ok(())
}

/// `_under_any`: whether `path` lies in one of the scopes.
pub fn under_any(path: Option<&str>, scopes: &[String]) -> bool {
    let Some(path) = path else {
        return false;
    };
    scopes.iter().any(|scope| {
        let normalized = scope.trim_matches('/');
        normalized.is_empty()
            || normalized == "."
            || path == normalized
            || path
                .strip_prefix(normalized)
                .is_some_and(|rest| rest.starts_with('/'))
    })
}

/// `_validate_scope`: each check path exists and holds a checked file.
pub fn scope(root: &Path, inventory: &Inventory, scopes: &[String]) -> Result<(), ()> {
    for scope in scopes {
        if !crate::checker::vfs::exists(&root.join(scope)) {
            return Err(());
        }
        let single = std::slice::from_ref(scope);
        if !inventory
            .check_paths
            .iter()
            .any(|path| under_any(Some(path), single))
        {
            return Err(());
        }
    }
    Ok(())
}

/// `relative_scope` for a path inside the project; `None` for one outside it,
/// which the Python CLI rejects.
pub fn relative_scope(root: &Path, cwd: &Path, raw: &str) -> Option<String> {
    let absolute = paths::resolve(&cwd.join(raw));
    let relative = absolute.strip_prefix(paths::resolve(root)).ok()?;
    let text = paths::posix(relative);
    Some(if text.is_empty() {
        ".".to_owned()
    } else {
        text
    })
}
