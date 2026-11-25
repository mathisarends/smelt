//! Top-level package discovery, as in `smelt.config.discovery`.
use super::paths;
use crate::checker::vfs;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use unicode_ident::{is_xid_continue, is_xid_start};

const IGNORED_DIRS: [&str; 13] = [
    "tests",
    "test",
    "docs",
    "doc",
    "scripts",
    "examples",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "migrations",
    "venv",
    "env",
];

fn is_identifier(name: &str) -> bool {
    let mut characters = name.chars();
    characters
        .next()
        .is_some_and(|first| first == '_' || is_xid_start(first))
        && characters.all(is_xid_continue)
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn has_python(directory: &Path) -> bool {
    let name = file_name(directory);
    vfs::is_dir(directory)
        && is_identifier(&name)
        && !IGNORED_DIRS.contains(&name.as_str())
        && paths::contains_match(directory, "*.py")
}

fn child_packages(directory: &Path) -> Vec<PathBuf> {
    if !vfs::is_dir(directory) {
        return Vec::new();
    }
    let mut children: Vec<PathBuf> = paths::children(directory)
        .into_iter()
        .filter(|child| has_python(child))
        .collect();
    children.sort_by_key(|child| file_name(child));
    children
}

/// Packages under an import root, including supported namespace packages.
pub fn packages_in(base: &Path) -> Vec<PathBuf> {
    let in_src = file_name(base) == "src";
    let settings_dir = if in_src {
        base.parent().unwrap_or(base)
    } else {
        base
    };
    let (declared, enabled) = build_package_settings(settings_dir);
    let namespace = in_src || enabled || !declared.is_empty();
    let found: Vec<PathBuf> = child_packages(base)
        .into_iter()
        .filter(|child| {
            paths::has_child_match(child, "*.py")
                || (namespace
                    && paths::children(child)
                        .iter()
                        .any(|entry| vfs::is_file(&entry.join("__init__.py"))))
        })
        .collect();
    if declared.is_empty() {
        return found;
    }
    let selected: Vec<PathBuf> = found
        .iter()
        .filter(|path| declared.contains(&file_name(path)))
        .cloned()
        .collect();
    if selected.is_empty() { found } else { selected }
}

/// The import root of a project or member: `src/` when it holds a package,
/// else the directory itself when it does.
pub fn import_root(directory: &Path) -> Option<PathBuf> {
    [directory.join("src"), directory.to_path_buf()]
        .into_iter()
        .find(|base| !packages_in(base).is_empty())
}

/// Top-level modules and namespace support declared by the uv build backend.
fn build_package_settings(directory: &Path) -> (BTreeSet<String>, bool) {
    let Ok(text) = paths::read_text(&directory.join("pyproject.toml")) else {
        return (BTreeSet::new(), false);
    };
    let Ok(data) = text.parse::<toml::Table>() else {
        return (BTreeSet::new(), false);
    };
    let backend = data
        .get("tool")
        .and_then(|tool| tool.get("uv"))
        .and_then(|uv| uv.get("build-backend"));
    let Some(backend) = backend else {
        return (BTreeSet::new(), false);
    };
    let top_level = |name: &str| name.split('.').next().unwrap_or(name).to_owned();
    let names = match backend.get("module-name") {
        Some(toml::Value::String(name)) => BTreeSet::from([top_level(name)]),
        Some(toml::Value::Array(items)) => items
            .iter()
            .filter_map(toml::Value::as_str)
            .map(top_level)
            .collect(),
        _ => BTreeSet::new(),
    };
    let namespace = backend.get("namespace").and_then(toml::Value::as_bool) == Some(true);
    (names, namespace)
}
