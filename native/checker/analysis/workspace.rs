//! Workspace ownership and declared member dependencies, like
//! `smelt.analysis.workspace_policy`.
use crate::checker::analysis::imports::ImportIndex;
use crate::checker::config::paths;
use crate::checker::config::workspace::member_directories;
use crate::checker::files::FileIndex;
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::io;
use std::sync::LazyLock;
use toml::{Table, Value};

static NAME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)").expect("valid pattern")
});
static SEPARATORS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[-_.]+").expect("valid pattern"));
static EXTRAS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\[\s*(?:[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\s*(?:,\s*[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\s*)*)?\]")
        .expect("valid pattern")
});
static SPECIFIERS: LazyLock<Regex> = LazyLock::new(|| {
    let one = r"(?:~=|===|==|!=|<=|>=|<|>)\s*[A-Za-z0-9_.*+!-]+";
    Regex::new(&format!(
        r"^(?:\(\s*{one}(?:\s*,\s*{one})*\s*\)|{one}(?:\s*,\s*{one})*)"
    ))
    .expect("valid pattern")
});

/// The declared uv workspace members of the analyzed root and their metadata.
pub struct WorkspaceIndex {
    /// Analyzed member paths, without the root.
    paths: Vec<String>,
    /// `pyproject.toml` of the root (`.`) and of every member; absent files are empty.
    metadata: Vec<(String, Table)>,
    /// Declared direct workspace dependencies; unknown or dynamic projects are absent.
    pub dependencies: HashMap<String, BTreeSet<String>>,
}

impl WorkspaceIndex {
    pub fn new(files: &FileIndex) -> Result<Self, String> {
        let paths = member_paths(files)?;
        let metadata = std::iter::once(".")
            .chain(paths.iter().map(String::as_str))
            .map(|member| Ok((member.to_owned(), read_project(files, member)?)))
            .collect::<Result<Vec<_>, String>>()?;
        let mut index = Self {
            paths,
            metadata,
            dependencies: HashMap::new(),
        };
        index.dependencies = index.declared(files)?;
        Ok(index)
    }

    pub fn member_for_module(&self, files: &FileIndex, module: &str) -> Option<&str> {
        if let Some(source) = files.sources.get(module) {
            return Some(self.member_for_path(&source.path));
        }
        let owners: HashSet<&str> = files
            .package_parts(module)
            .iter()
            .map(|part| self.member_for_path(part))
            .collect();
        match owners.len() {
            1 => owners.into_iter().next(),
            _ => None,
        }
    }

    pub fn member_for_path(&self, path: &str) -> &str {
        member_for_path(&self.paths, path)
    }

    /// The `project` table of a member, when it has one.
    pub fn project(&self, member: &str) -> Option<&Table> {
        self.metadata
            .iter()
            .find(|(path, _)| path == member)
            .and_then(|(_, data)| data.get("project"))
            .and_then(Value::as_table)
    }

    fn declared(&self, files: &FileIndex) -> Result<HashMap<String, BTreeSet<String>>, String> {
        let names = self.project_names()?;
        let root_sources = self
            .metadata
            .first()
            .map(|(_, data)| sources(data))
            .unwrap_or_default();
        let mut declared = HashMap::new();
        for (path, data) in &self.metadata {
            let Some(project) = data.get("project").and_then(Value::as_table) else {
                continue;
            };
            let Some(dependencies) = requirements(project, path)? else {
                continue;
            };
            let local_sources = sources(data);
            let mut all_sources = root_sources.clone();
            all_sources.extend(
                local_sources
                    .iter()
                    .map(|(name, value)| (name.clone(), *value)),
            );
            let mut targets = BTreeSet::new();
            for dependency in dependencies {
                let name = canonicalize_name(&requirement_name(&dependency).map_err(|error| {
                    format!("{path}/pyproject.toml: invalid dependency {dependency:?}: {error}")
                })?);
                let Some(member) = names.get(&name) else {
                    continue;
                };
                let Some(source) = all_sources.get(&name) else {
                    targets.insert(member.clone());
                    continue;
                };
                for entry in source_entries(source) {
                    if entry.get("workspace").and_then(Value::as_bool) == Some(true) {
                        targets.insert(member.clone());
                    }
                    if let Some(target) = entry.get("path").and_then(Value::as_str) {
                        let base = if local_sources.contains_key(&name) {
                            path.as_str()
                        } else {
                            "."
                        };
                        let directory = paths::resolve(&files.root.join(base).join(target));
                        if paths::resolve(&files.root.join(member)) == directory {
                            targets.insert(member.clone());
                        }
                    }
                }
            }
            declared.insert(path.clone(), targets);
        }
        Ok(declared)
    }

    fn project_names(&self) -> Result<HashMap<String, String>, String> {
        let mut names: HashMap<String, String> = HashMap::new();
        for (path, data) in &self.metadata {
            let Some(name) = data
                .get("project")
                .and_then(Value::as_table)
                .and_then(|project| project.get("name"))
                .and_then(Value::as_str)
            else {
                continue;
            };
            let normalized = canonicalize_name(name);
            if let Some(previous) = names.get(&normalized) {
                return Err(format!(
                    "duplicate workspace project name \"{name}\" in {previous} and {path}"
                ));
            }
            names.insert(normalized, path.clone());
        }
        Ok(names)
    }
}

/// The first-party imports from one analyzed member into another, sorted.
pub fn member_edges(
    files: &FileIndex,
    imports: &ImportIndex,
) -> Result<Vec<(String, String)>, String> {
    let members = member_paths(files)?;
    let owner = |module: &str| -> Option<&str> {
        if let Some(source) = files.sources.get(module) {
            return Some(member_for_path(&members, &source.path));
        }
        let owners: HashSet<&str> = files
            .package_parts(module)
            .iter()
            .map(|part| member_for_path(&members, part))
            .collect();
        match owners.len() {
            1 => owners.into_iter().next(),
            _ => None,
        }
    };
    let edges: BTreeSet<(String, String)> = imports
        .all_imports()
        .filter(|detail| !detail.external)
        .filter_map(|detail| {
            let source = owner(&detail.importer)?;
            let target = owner(&detail.imported)?;
            (source != target).then(|| (source.to_owned(), target.to_owned()))
        })
        .collect();
    Ok(edges.into_iter().collect())
}

/// The analyzed member paths of the workspace, without the root.
pub fn member_paths(files: &FileIndex) -> Result<Vec<String>, String> {
    Ok(member_directories(&files.root, false)?
        .into_iter()
        .filter(|member| member.skipped.is_none())
        .map(|member| member.path)
        .collect())
}

/// The distribution name of the workspace root project, if there is one.
pub fn root_project_name(files: &FileIndex) -> Result<Option<String>, String> {
    let data = read_project(files, ".")?;
    Ok(data
        .get("project")
        .and_then(|project| project.get("name"))
        .and_then(Value::as_str)
        .map(str::to_owned))
}

/// The innermost member holding `path`, `.` for files of the root.
pub fn member_for_path<'m>(members: &'m [String], path: &str) -> &'m str {
    members
        .iter()
        .filter(|member| {
            path == member.as_str()
                || path
                    .strip_prefix(member.as_str())
                    .is_some_and(|rest| rest.starts_with('/'))
        })
        .max_by_key(|member| member.len())
        .map_or(".", String::as_str)
}

fn read_project(files: &FileIndex, member: &str) -> Result<Table, String> {
    let text = match paths::read_text(&files.root.join(member).join("pyproject.toml")) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Table::new()),
        Err(error) => {
            return Err(format!(
                "{member}/pyproject.toml: cannot read workspace metadata: {error}"
            ));
        }
    };
    text.parse::<Table>().map_err(|error| {
        format!("{member}/pyproject.toml: cannot read workspace metadata: {error}")
    })
}

/// `tool.uv.sources` by normalized distribution name.
fn sources(data: &Table) -> BTreeMap<String, &Value> {
    let mut table = data;
    for key in ["tool", "uv", "sources"] {
        match table.get(key) {
            None => return BTreeMap::new(),
            Some(Value::Table(inner)) => table = inner,
            Some(_) => return BTreeMap::new(),
        }
    }
    table
        .iter()
        .map(|(name, value)| (canonicalize_name(name), value))
        .collect()
}

fn source_entries(value: &Value) -> Vec<&Table> {
    match value {
        Value::Table(entry) => vec![entry],
        Value::Array(entries) => entries.iter().filter_map(Value::as_table).collect(),
        _ => Vec::new(),
    }
}

/// Every project and optional dependency; `None` when they are dynamic.
fn requirements(project: &Table, path: &str) -> Result<Option<Vec<String>>, String> {
    let dynamic = match project.get("dynamic") {
        None => &[][..],
        Some(Value::Array(items)) => items.as_slice(),
        Some(_) => {
            return Err(format!(
                "{path}/pyproject.toml: project.dynamic must be a list"
            ));
        }
    };
    if dynamic.iter().any(|field| {
        matches!(
            field.as_str(),
            Some("dependencies" | "optional-dependencies")
        )
    }) {
        return Ok(None);
    }
    let empty = Table::new();
    let optional = match project.get("optional-dependencies") {
        None => &empty,
        Some(Value::Table(table)) => table,
        Some(_) => {
            return Err(format!(
                "{path}/pyproject.toml: project.optional-dependencies must be a table"
            ));
        }
    };
    let none = Value::Array(Vec::new());
    let groups = std::iter::once((
        "dependencies".to_owned(),
        project.get("dependencies").unwrap_or(&none),
    ))
    .chain(
        optional
            .iter()
            .map(|(extra, values)| (format!("optional-dependencies.{extra}"), values)),
    );
    let mut found = Vec::new();
    for (field, values) in groups {
        let strings = values
            .as_array()
            .and_then(|items| items.iter().map(Value::as_str).collect::<Option<Vec<_>>>());
        let Some(strings) = strings else {
            return Err(format!(
                "{path}/pyproject.toml: project.{field} must be a list of requirements"
            ));
        };
        found.extend(strings.into_iter().map(str::to_owned));
    }
    Ok(Some(found))
}

/// `packaging.utils.canonicalize_name`.
pub fn canonicalize_name(name: &str) -> String {
    SEPARATORS.replace_all(name, "-").to_lowercase()
}

/// The distribution name of a PEP 508 requirement.
///
/// The structure is checked: name, extras, then a URL or version specifiers, then an
/// optional marker. Marker expressions are not evaluated or checked.
fn requirement_name(requirement: &str) -> Result<String, String> {
    let Some(found) = NAME.captures(requirement) else {
        return Err("Expected package name at the start of dependency specifier".to_owned());
    };
    let name = found[1].to_owned();
    let mut rest = requirement[found[0].len()..].trim_start();
    if rest.starts_with('[') {
        let Some(extras) = EXTRAS.find(rest) else {
            return Err("Expected extra name or closing bracket".to_owned());
        };
        rest = rest[extras.end()..].trim_start();
    }
    if let Some(url) = rest.strip_prefix('@') {
        let url = url.trim_start();
        let end = url.find(char::is_whitespace).unwrap_or(url.len());
        if end == 0 {
            return Err("Expected URL after @".to_owned());
        }
        rest = url[end..].trim_start();
    } else if let Some(specifiers) = SPECIFIERS.find(rest) {
        rest = rest[specifiers.end()..].trim_start();
    }
    match rest.strip_prefix(';') {
        Some(marker) if !marker.trim().is_empty() => Ok(name),
        Some(_) => Err("Expected marker after semicolon".to_owned()),
        None if rest.is_empty() => Ok(name),
        None => Err(format!("Unexpected text after the requirement: {rest:?}")),
    }
}

#[cfg(test)]
mod tests {
    use super::{canonicalize_name, requirement_name};

    #[test]
    fn normalizes_distribution_names() {
        assert_eq!(canonicalize_name("Core_Lib.Utils"), "core-lib-utils");
        assert_eq!(canonicalize_name("a--b__c"), "a-b-c");
    }

    #[test]
    fn reads_requirement_names() {
        for (requirement, name) in [
            ("core-lib", "core-lib"),
            (
                "core_lib[extra, other] >=1.0, <2 ; python_version >= '3.12'",
                "core_lib",
            ),
            ("api @ file:///workspace/api", "api"),
            ("tool (>=1.0)", "tool"),
            ("  spaced", "spaced"),
        ] {
            assert_eq!(requirement_name(requirement).as_deref(), Ok(name));
        }
        for invalid in ["", ">=1.0", "name[", "name @", "name ;", "name garbage"] {
            assert!(requirement_name(invalid).is_err(), "{invalid}");
        }
    }
}
