//! Declared minimum Python versions, with per-project provenance.
use regex::Regex;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::{LazyLock, Mutex};

const PRE: &str = r"[-_\.]?(?:alpha|beta|preview|pre|a|b|c|rc)[-_\.]?[0-9]*";
const POST: &str = r"(?:-[0-9]+|[-_\.]?(?:post|rev|r)[-_\.]?[0-9]*)";
const DEV: &str = r"[-_\.]?dev[-_\.]?[0-9]*";
const LOCAL: &str = r"\+[a-z0-9]+(?:[-_\.][a-z0-9]+)*";

static SPECIFIER: LazyLock<Regex> = LazyLock::new(|| {
    let suffix = format!("(?:{PRE})?(?:{POST})?(?:{DEV})?");
    let pattern = format!(
        r"(?i)^\s*(?:===\s*[^\s;)]*|(?:==|!=)\s*v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:\.\*|{suffix}(?:{LOCAL})?)?|~=\s*v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)+{suffix}|(?:<=|>=|<|>)\s*v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*{suffix})\s*$"
    );
    Regex::new(&pattern).expect("the specifier pattern is valid")
});

static VERSION: LazyLock<Regex> = LazyLock::new(|| {
    let pattern = format!(
        r"(?i)^\s*v?(?:(?P<epoch>[0-9]+)!)?(?P<release>[0-9]+(?:\.[0-9]+)*)(?P<pre>{PRE})?(?:{POST})?(?P<dev>{DEV})?(?:{LOCAL})?\s*$"
    );
    Regex::new(&pattern).expect("the version pattern is valid")
});

#[derive(Clone)]
pub struct PythonTarget {
    pub minimum: Option<(u64, u64)>,
    source: Option<String>,
    requires_python: Option<String>,
    uncertainty: Option<String>,
}

impl PythonTarget {
    fn unknown(source: Option<String>, spec: Option<&str>, uncertainty: String) -> Self {
        Self {
            minimum: None,
            source,
            requires_python: spec.map(str::to_owned),
            uncertainty: Some(uncertainty),
        }
    }

    /// Whether the declared minimum is known to be older than `major.minor`.
    pub fn below(&self, major: u64, minor: u64) -> bool {
        self.minimum.is_some_and(|minimum| minimum < (major, minor))
    }

    /// Whether every supported interpreter defers annotations (Python 3.14+).
    pub fn deferred_annotations(&self) -> bool {
        self.minimum
            .is_some_and(|(major, minor)| major == 3 && minor >= 14)
    }

    pub fn to_json(&self) -> Value {
        json!({
            "minimum": self.minimum.map(|(major, minor)| format!("{major}.{minor}")),
            "source": self.source,
            "requires_python": self.requires_python,
            "uncertainty": self.uncertainty,
        })
    }
}

pub struct PythonTargets {
    root: PathBuf,
    source_roots: Vec<PathBuf>,
    pinned: Option<(u64, u64)>,
    cache: Mutex<HashMap<PathBuf, PythonTarget>>,
}

impl PythonTargets {
    pub fn new(root: &Path, source_roots: &[String], pinned: Option<&str>) -> Self {
        Self {
            root: root.to_path_buf(),
            source_roots: source_roots
                .iter()
                .map(|entry| {
                    let joined = root.join(entry);
                    fs::canonicalize(&joined).unwrap_or(joined)
                })
                .collect(),
            pinned: pinned.and_then(|value| {
                let (major, minor) = value.split_once('.')?;
                Some((major.parse().ok()?, minor.parse().ok()?))
            }),
            cache: Mutex::new(HashMap::new()),
        }
    }

    pub fn for_path(&self, path: &Path) -> PythonTarget {
        if let Some(minimum) = self.pinned {
            return PythonTarget {
                minimum: Some(minimum),
                source: Some("project.python_version".to_owned()),
                requires_python: None,
                uncertainty: None,
            };
        }
        let parent = path.parent().unwrap_or(path);
        let directory = fs::canonicalize(parent).unwrap_or_else(|_| parent.to_path_buf());
        let mut boundary = self.root.as_path();
        if !directory.starts_with(&self.root) {
            let Some(deepest) = self
                .source_roots
                .iter()
                .filter(|entry| directory.starts_with(entry))
                .max_by_key(|entry| entry.components().count())
            else {
                return PythonTarget::unknown(
                    None,
                    None,
                    "path is outside the declared source roots".to_owned(),
                );
            };
            boundary = deepest.parent().unwrap_or(deepest);
        }
        let mut cache = self.cache.lock().expect("the cache lock is not poisoned");
        if let Some(target) = cache.get(&directory) {
            return target.clone();
        }
        let mut visited = Vec::new();
        let mut found = None;
        for current in directory.ancestors() {
            if !current.starts_with(boundary) {
                break;
            }
            visited.push(current.to_path_buf());
            if let Some(target) = cache.get(current) {
                found = Some(target.clone());
                break;
            }
            let metadata = current.join("pyproject.toml");
            if metadata.is_file()
                && let Some(target) = self.read(&metadata)
            {
                found = Some(target);
                break;
            }
        }
        let result = found.unwrap_or_else(|| {
            PythonTarget::unknown(None, None, "no declared minimum Python version".to_owned())
        });
        for current in visited {
            cache.insert(current, result.clone());
        }
        result
    }

    fn read(&self, path: &Path) -> Option<PythonTarget> {
        let source = relative(path, &self.root);
        let data = fs::read_to_string(path)
            .map_err(|error| error.to_string())
            .and_then(|text| {
                text.parse::<toml::Table>()
                    .map_err(|error| error.to_string())
            });
        let data = match data {
            Ok(data) => data,
            Err(error) => {
                return Some(PythonTarget::unknown(
                    Some(source),
                    None,
                    format!("cannot read Python metadata: {error}"),
                ));
            }
        };
        let project = data.get("project")?;
        let Some(table) = project.as_table() else {
            return Some(PythonTarget::unknown(
                Some(source),
                None,
                "project metadata is not a table".to_owned(),
            ));
        };
        let Some(spec) = table.get("requires-python").and_then(toml::Value::as_str) else {
            return Some(PythonTarget::unknown(
                Some(source),
                None,
                "project has no string requires-python".to_owned(),
            ));
        };
        Some(minimum_from_requires_python(spec, source))
    }
}

/// A conservative lower bound. Exclusions never raise the inferred minimum.
fn minimum_from_requires_python(spec: &str, source: String) -> PythonTarget {
    let unknown =
        |source: String, reason: String| PythonTarget::unknown(Some(source), Some(spec), reason);
    let items: Vec<&str> = spec
        .split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .collect();
    if let Some(invalid) = items.iter().find(|item| !SPECIFIER.is_match(item)) {
        let quoted = super::repr::str_repr(invalid);
        return unknown(
            source,
            format!("invalid requires-python: Invalid specifier: {quoted}"),
        );
    }
    let mut best: Option<(u64, Vec<u64>)> = None;
    for item in items {
        let (operator, version) = split_specifier(item);
        match operator {
            ">=" | ">" | "~=" | "==" => {
                let text = version.strip_suffix(".*").unwrap_or(version);
                let Some(captures) = VERSION.captures(text) else {
                    let quoted = super::repr::str_repr(text);
                    return unknown(
                        source,
                        format!("invalid requires-python: Invalid version: {quoted}"),
                    );
                };
                if captures.name("pre").is_some() || captures.name("dev").is_some() {
                    return unknown(
                        source,
                        "prerelease minimum is not a stable Python target".to_owned(),
                    );
                }
                let epoch = captures
                    .name("epoch")
                    .and_then(|value| value.as_str().parse().ok())
                    .unwrap_or(0);
                let release: Vec<u64> = captures["release"]
                    .split('.')
                    .map(|part| part.parse().unwrap_or(u64::MAX))
                    .collect();
                let key = |entry: &(u64, Vec<u64>)| {
                    let mut trimmed = entry.1.clone();
                    while trimmed.last() == Some(&0) {
                        trimmed.pop();
                    }
                    (entry.0, trimmed)
                };
                let candidate = (epoch, release);
                if best
                    .as_ref()
                    .is_none_or(|current| key(&candidate) > key(current))
                {
                    best = Some(candidate);
                }
            }
            "===" => {
                return unknown(
                    source,
                    "arbitrary version equality has no inferred Python minimum".to_owned(),
                );
            }
            _ => {}
        }
    }
    let Some((_, release)) = best else {
        return unknown(
            source,
            "requires-python has no explicit lower bound".to_owned(),
        );
    };
    PythonTarget {
        minimum: Some((release[0], release.get(1).copied().unwrap_or(0))),
        source: Some(source),
        requires_python: Some(spec.to_owned()),
        uncertainty: None,
    }
}

fn split_specifier(item: &str) -> (&str, &str) {
    let length = if item.starts_with("===") {
        3
    } else if ["~=", "==", "!=", "<=", ">="]
        .iter()
        .any(|operator| item.starts_with(operator))
    {
        2
    } else {
        1
    };
    (&item[..length], item[length..].trim())
}

/// A POSIX path from `root`, with `..` when `path` lies outside it.
fn relative(path: &Path, root: &Path) -> String {
    if let Ok(inside) = path.strip_prefix(root) {
        return posix(inside);
    }
    let from: Vec<Component<'_>> = root.components().collect();
    let to: Vec<Component<'_>> = path.components().collect();
    let shared = from.iter().zip(&to).take_while(|(a, b)| a == b).count();
    if shared == 0 {
        return posix(path);
    }
    let mut parts = vec!["..".to_owned(); from.len() - shared];
    parts.extend(
        to[shared..]
            .iter()
            .map(|part| part.as_os_str().to_string_lossy().into_owned()),
    );
    parts.join("/")
}

fn posix(path: &Path) -> String {
    path.components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn minimum(spec: &str) -> (Option<(u64, u64)>, Option<String>) {
        let target = minimum_from_requires_python(spec, "pyproject.toml".to_owned());
        (target.minimum, target.uncertainty)
    }

    #[test]
    fn bounds_follow_the_highest_lower_bound() {
        assert_eq!(minimum(">=3.12").0, Some((3, 12)));
        assert_eq!(minimum(">=3.10,<4,>=3.11.2").0, Some((3, 11)));
        assert_eq!(minimum("~=3.9").0, Some((3, 9)));
        assert_eq!(minimum("==3.13.*").0, Some((3, 13)));
        assert_eq!(minimum(">=3").0, Some((3, 0)));
    }

    #[test]
    fn unusable_specifiers_stay_uncertain() {
        assert_eq!(
            minimum("<4").1.as_deref(),
            Some("requires-python has no explicit lower bound")
        );
        assert_eq!(
            minimum(">=3.14a1").1.as_deref(),
            Some("prerelease minimum is not a stable Python target")
        );
        assert_eq!(
            minimum("===3.12").1.as_deref(),
            Some("arbitrary version equality has no inferred Python minimum")
        );
        assert_eq!(
            minimum("python3").1.as_deref(),
            Some("invalid requires-python: Invalid specifier: 'python3'")
        );
    }
}
