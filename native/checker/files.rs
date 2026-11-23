//! Source discovery with the package rules of `smelt.analysis.files`.
use super::config::Request;
use super::patterns::PathPattern;
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::path::{Component, Path, PathBuf};

const SKIP_DIRS: [&str; 5] = ["__pycache__", ".git", ".venv", "venv", "node_modules"];

pub struct SourceFile {
    pub module: String,
    pub path: String,
    pub absolute: PathBuf,
    /// An `__init__.py`.
    pub is_package: bool,
}

pub struct FileIndex {
    pub root: PathBuf,
    pub sources: BTreeMap<String, SourceFile>,
    pub packages: Vec<String>,
}

impl FileIndex {
    pub fn discover(request: &Request) -> Result<Self, String> {
        let root = fs::canonicalize(&request.root)
            .map_err(|error| format!("cannot resolve project root: {error}"))?;
        let exclude = request
            .project
            .exclude
            .iter()
            .map(|pattern| PathPattern::new(pattern))
            .collect::<Result<Vec<_>, _>>()?;
        let mut discovery = Discovery {
            root,
            exclude,
            sources: BTreeMap::new(),
            portions: HashMap::new(),
        };
        for package in &request.project.root_packages {
            discovery.package_root(package, &request.project.source_roots)?;
        }
        let mut packages: Vec<String> = discovery.portions.into_keys().collect();
        packages.sort();
        Ok(Self {
            root: discovery.root,
            sources: discovery.sources,
            packages,
        })
    }
}

struct Discovery {
    root: PathBuf,
    exclude: Vec<PathPattern>,
    sources: BTreeMap<String, SourceFile>,
    portions: HashMap<String, (String, bool)>,
}

impl Discovery {
    fn package_root(&mut self, package: &str, source_roots: &[String]) -> Result<(), String> {
        let mut candidates: Vec<PathBuf> = Vec::new();
        for source_root in source_roots {
            let directory = self.root.join(source_root).join(package);
            if !directory.is_dir() || self.excluded(&self.relative(&directory)) {
                continue;
            }
            let resolved = fs::canonicalize(&directory).map_err(|error| error.to_string())?;
            if !candidates.contains(&resolved) {
                candidates.push(resolved);
            }
        }
        let regular: Vec<PathBuf> = candidates
            .iter()
            .filter(|path| path.join("__init__.py").is_file())
            .cloned()
            .collect();
        if regular.len() > 1 {
            let paths: Vec<String> = regular.iter().map(|path| self.relative(path)).collect();
            return Err(format!("ambiguous package {package}: {}", paths.join(", ")));
        }
        // Python gives an ordinary package precedence over namespace portions.
        let selected = if regular.is_empty() {
            candidates
        } else {
            regular
        };
        for directory in selected {
            self.walk_package(&directory, package)?;
        }
        Ok(())
    }

    fn walk_package(&mut self, directory: &Path, module: &str) -> Result<(), String> {
        let relative = self.relative(directory);
        if self.excluded(&relative) {
            return Ok(());
        }
        let has_init = directory.join("__init__.py").is_file();
        if let Some((previous, previous_init)) = self.portions.get(module)
            && (*previous_init || has_init)
        {
            return Err(format!(
                "ambiguous package {module}: {previous}, {relative}"
            ));
        }
        self.portions
            .entry(module.to_owned())
            .or_insert((relative, has_init));
        let mut entries: Vec<PathBuf> = fs::read_dir(directory)
            .map_err(|error| error.to_string())?
            .filter_map(|entry| entry.ok().map(|item| item.path()))
            .collect();
        entries.sort_by_key(|path| file_name(path).to_owned());
        for entry in entries {
            let name = file_name(&entry).to_owned();
            if entry.is_dir() {
                if !SKIP_DIRS.contains(&name.as_str())
                    && is_identifier(&name)
                    && contains_python(&entry)
                {
                    self.walk_package(&entry, &format!("{module}.{name}"))?;
                }
            } else if entry.extension().is_some_and(|suffix| suffix == "py") && entry.is_file() {
                self.add_file(&entry, module, &name)?;
            }
        }
        Ok(())
    }

    fn add_file(&mut self, entry: &Path, package: &str, name: &str) -> Result<(), String> {
        let relative = self.relative(entry);
        if self.excluded(&relative) {
            return Ok(());
        }
        let stem = entry
            .file_stem()
            .and_then(|stem| stem.to_str())
            .unwrap_or_default();
        let module = if name == "__init__.py" {
            package.to_owned()
        } else if is_identifier(stem) {
            format!("{package}.{stem}")
        } else {
            return Ok(());
        };
        if let Some(previous) = self.sources.get(&module) {
            return Err(format!(
                "ambiguous module {module}: {}, {relative}",
                previous.path
            ));
        }
        self.sources.insert(
            module.clone(),
            SourceFile {
                module,
                path: relative,
                absolute: entry.to_path_buf(),
                is_package: name == "__init__.py",
            },
        );
        Ok(())
    }

    fn excluded(&self, path: &str) -> bool {
        self.exclude.iter().any(|pattern| pattern.matches(path))
    }

    fn relative(&self, path: &Path) -> String {
        let resolved = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        let relative = resolved.strip_prefix(&self.root).unwrap_or(&resolved);
        relative
            .components()
            .filter_map(|component| match component {
                Component::Normal(part) => Some(part.to_string_lossy().into_owned()),
                _ => None,
            })
            .collect::<Vec<_>>()
            .join("/")
    }
}

fn file_name(path: &Path) -> &str {
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default()
}

fn contains_python(directory: &Path) -> bool {
    let mut pending = vec![directory.to_path_buf()];
    let mut visited = Vec::new();
    while let Some(parent) = pending.pop() {
        let resolved = fs::canonicalize(&parent).unwrap_or_else(|_| parent.clone());
        if visited.contains(&resolved) {
            continue;
        }
        visited.push(resolved);
        let Ok(entries) = fs::read_dir(&parent) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let name = file_name(&path);
                if !SKIP_DIRS.contains(&name) && !name.starts_with('.') {
                    pending.push(path);
                }
            } else if path.extension().is_some_and(|suffix| suffix == "py") {
                return true;
            }
        }
    }
    false
}

/// `str.isidentifier`.
pub fn is_identifier(name: &str) -> bool {
    let mut chars = name.chars();
    chars
        .next()
        .is_some_and(|first| first == '_' || unicode_ident::is_xid_start(first))
        && chars.all(unicode_ident::is_xid_continue)
}
