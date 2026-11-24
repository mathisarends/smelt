//! Source discovery with the package rules of `smelt.analysis.files`.
use super::config::Request;
use super::patterns::PathPattern;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::{Component, Path, PathBuf};

const SKIP_DIRS: [&str; 5] = ["__pycache__", ".git", ".venv", "venv", "node_modules"];
const YAML_SKIP_DIRS: [&str; 9] = [
    ".hg",
    ".svn",
    ".smelt_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    ".nox",
    ".worktrees",
];

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
    /// Test files by project-relative path.
    pub tests: BTreeMap<String, SourceFile>,
    pub packages: Vec<String>,
    /// Packages without an initializer.
    pub namespace_packages: Vec<String>,
    /// The root packages that exist under a source root.
    pub found_roots: Vec<String>,
    /// Source modules in discovery order, which findings listing peers follow.
    pub source_order: Vec<String>,
    package_paths: HashMap<String, String>,
    /// Every directory of each package: one, or several namespace portions.
    package_parts: HashMap<String, Vec<String>>,
    /// The configured source root each package was found under.
    package_source_roots: HashMap<String, String>,
    /// The declared test root that owns each test, the most specific one.
    test_roots: HashMap<String, String>,
    default_source_root: String,
    exclude: Vec<PathPattern>,
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
            parts: HashMap::new(),
            found_roots: Vec::new(),
            source_order: Vec::new(),
        };
        for package in &request.project.root_packages {
            discovery.package_root(package, &request.project.source_roots)?;
        }
        let (tests, test_roots) = discovery.tests(&request.project.test_roots);
        let namespace_packages = discovery
            .portions
            .iter()
            .filter(|(_, (_, has_init, _))| !has_init)
            .map(|(module, _)| module.clone())
            .collect();
        let package_source_roots: HashMap<String, String> = discovery
            .portions
            .iter()
            .map(|(module, (_, _, source_root))| (module.clone(), source_root.clone()))
            .collect();
        let package_paths: HashMap<String, String> = discovery
            .portions
            .into_iter()
            .map(|(module, (path, _, _))| (module, path))
            .collect();
        let mut packages: Vec<String> = package_paths.keys().cloned().collect();
        packages.sort();
        Ok(Self {
            root: discovery.root,
            sources: discovery.sources,
            tests,
            packages,
            namespace_packages,
            found_roots: discovery.found_roots,
            source_order: discovery.source_order,
            package_paths,
            package_parts: discovery.parts,
            package_source_roots,
            test_roots,
            default_source_root: request
                .project
                .source_roots
                .first()
                .cloned()
                .unwrap_or_else(|| ".".to_owned()),
            exclude: discovery.exclude,
        })
    }

    /// The test root that owns a discovered test.
    pub fn test_root(&self, path: &str) -> Option<&str> {
        self.test_roots.get(path).map(String::as_str)
    }

    /// The source root a discovered package was found under.
    pub fn package_source_root(&self, package: &str) -> Option<&str> {
        self.package_source_roots.get(package).map(String::as_str)
    }

    /// Whether `path` is a discovered test or lies below a declared test root.
    pub fn is_test_path(&self, path: &str, test_roots: &[String]) -> bool {
        self.tests.contains_key(path)
            || test_roots.iter().any(|root| {
                let root = root.trim_matches('/');
                root == "." || path == root || path.starts_with(&format!("{root}/"))
            })
    }

    /// The source root of the innermost package that contains `module`.
    pub fn source_root_dir(&self, module: &str) -> Option<&str> {
        let parts: Vec<&str> = module.split('.').collect();
        (1..=parts.len()).rev().find_map(|end| {
            self.package_source_roots
                .get(&parts[..end].join("."))
                .map(String::as_str)
        })
    }

    /// The expected project-relative path of `module`, whether or not it exists.
    pub fn module_to_path(&self, module: &str, package: bool) -> String {
        let root = self
            .source_root_dir(module)
            .unwrap_or(&self.default_source_root);
        let mut parts: Vec<&str> = root
            .trim_matches('/')
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .collect();
        parts.extend(module.split('.'));
        format!("{}{}", parts.join("/"), if package { "/" } else { ".py" })
    }

    /// The initializer of a package or the file of a module, else the package directory.
    /// The directories of a package: one, or each portion of a namespace package.
    pub fn package_parts(&self, module: &str) -> &[String] {
        self.package_parts.get(module).map_or(&[], Vec::as_slice)
    }

    pub fn path_for_module(&self, module: &str) -> Option<&str> {
        self.sources
            .get(module)
            .map(|source| source.path.as_str())
            .or_else(|| self.package_paths.get(module).map(String::as_str))
    }

    /// YAML files below the root by name alone; their contents are never read.
    pub fn yaml_paths(&self) -> Vec<String> {
        let mut found = BTreeSet::new();
        let mut visited = HashSet::new();
        let mut pending = vec![self.root.clone()];
        while let Some(directory) = pending.pop() {
            let resolved = fs::canonicalize(&directory).unwrap_or_else(|_| directory.clone());
            if !resolved.starts_with(&self.root) || !visited.insert(resolved) {
                continue;
            }
            let Ok(entries) = fs::read_dir(&directory) else {
                continue;
            };
            let mut entries: Vec<PathBuf> = entries.flatten().map(|entry| entry.path()).collect();
            entries.sort();
            for entry in entries {
                let Ok(inside) = entry.strip_prefix(&self.root) else {
                    continue;
                };
                let relative = posix_parts(inside).join("/");
                if self
                    .exclude
                    .iter()
                    .any(|pattern| pattern.matches(&relative))
                {
                    continue;
                }
                if entry.is_dir() {
                    let name = file_name(&entry);
                    if !SKIP_DIRS.contains(&name) && !YAML_SKIP_DIRS.contains(&name) {
                        pending.push(entry);
                    }
                } else if entry
                    .extension()
                    .is_some_and(|suffix| suffix == "yaml" || suffix == "yml")
                    && entry.is_file()
                    && fs::canonicalize(&entry).is_ok_and(|real| real.starts_with(&self.root))
                {
                    found.insert(relative);
                }
            }
        }
        found.into_iter().collect()
    }
}

struct Discovery {
    root: PathBuf,
    exclude: Vec<PathPattern>,
    sources: BTreeMap<String, SourceFile>,
    /// Package to its directory, whether it has an initializer, and its source root.
    portions: HashMap<String, (String, bool, String)>,
    /// Package to every directory found for it, in discovery order.
    parts: HashMap<String, Vec<String>>,
    found_roots: Vec<String>,
    source_order: Vec<String>,
}

impl Discovery {
    fn package_root(&mut self, package: &str, source_roots: &[String]) -> Result<(), String> {
        // A later root naming the same directory owns it, like a dict comprehension.
        let mut candidates: Vec<(PathBuf, &str)> = Vec::new();
        for source_root in source_roots {
            let directory = self.root.join(source_root).join(package);
            if !directory.is_dir() || self.excluded(&self.relative(&directory)) {
                continue;
            }
            let resolved = fs::canonicalize(&directory).map_err(|error| error.to_string())?;
            match candidates.iter_mut().find(|(path, _)| *path == resolved) {
                Some(entry) => entry.1 = source_root,
                None => candidates.push((resolved, source_root)),
            }
        }
        let regular: Vec<(PathBuf, &str)> = candidates
            .iter()
            .filter(|(path, _)| path.join("__init__.py").is_file())
            .cloned()
            .collect();
        if regular.len() > 1 {
            let paths: Vec<String> = regular
                .iter()
                .map(|(path, _)| self.relative(path))
                .collect();
            return Err(format!("ambiguous package {package}: {}", paths.join(", ")));
        }
        // Python gives an ordinary package precedence over namespace portions.
        let selected = if regular.is_empty() {
            candidates
        } else {
            regular
        };
        if !selected.is_empty() {
            self.found_roots.push(package.to_owned());
        }
        for (directory, source_root) in selected {
            self.walk_package(&directory, package, source_root)?;
        }
        Ok(())
    }

    fn walk_package(
        &mut self,
        directory: &Path,
        module: &str,
        source_root: &str,
    ) -> Result<(), String> {
        let relative = self.relative(directory);
        if self.excluded(&relative) {
            return Ok(());
        }
        let has_init = directory.join("__init__.py").is_file();
        if let Some((previous, previous_init, _)) = self.portions.get(module)
            && (*previous_init || has_init)
        {
            return Err(format!(
                "ambiguous package {module}: {previous}, {relative}"
            ));
        }
        self.parts
            .entry(module.to_owned())
            .or_default()
            .push(relative.clone());
        self.portions.entry(module.to_owned()).or_insert((
            relative,
            has_init,
            source_root.to_owned(),
        ));
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
                    self.walk_package(&entry, &format!("{module}.{name}"), source_root)?;
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
        self.source_order.push(module.clone());
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

    /// Test files below the declared roots that are neither sources nor excluded.
    fn tests(&self, roots: &[String]) -> (BTreeMap<String, SourceFile>, HashMap<String, String>) {
        let source_paths: HashSet<&str> = self
            .sources
            .values()
            .map(|source| source.path.as_str())
            .collect();
        let mut tests = BTreeMap::new();
        let mut owners = HashMap::new();
        // Broad roots first, so the most specific declared root owns a test.
        let mut ordered: Vec<(usize, &String)> = roots
            .iter()
            .map(|root| {
                let directory = self.root.join(root);
                let resolved = fs::canonicalize(&directory).unwrap_or(directory);
                (resolved.components().count(), root)
            })
            .collect();
        ordered.sort();
        for (_, root) in ordered {
            let directory = self.root.join(root);
            if !directory.is_dir() {
                continue;
            }
            for path in python_files(&directory) {
                let relative = self.relative(&path);
                if source_paths.contains(relative.as_str()) || self.excluded(&relative) {
                    continue;
                }
                let stem = path
                    .file_stem()
                    .and_then(|stem| stem.to_str())
                    .unwrap_or_default();
                let name = file_name(&path);
                if name == "conftest.py" || name.starts_with("test_") || stem.ends_with("_test") {
                    let module = relative
                        .strip_suffix(".py")
                        .unwrap_or(&relative)
                        .replace('/', ".");
                    owners.insert(relative.clone(), root.trim_matches('/').to_owned());
                    tests.insert(
                        relative.clone(),
                        SourceFile {
                            module,
                            path: relative,
                            absolute: path,
                            is_package: false,
                        },
                    );
                }
            }
        }
        (tests, owners)
    }

    fn excluded(&self, path: &str) -> bool {
        self.exclude.iter().any(|pattern| pattern.matches(path))
    }

    fn relative(&self, path: &Path) -> String {
        relative_path(&self.root, path)
    }
}

/// The POSIX path of `path` from the resolved `root`, with `..` for a path outside it.
pub fn relative_path(root: &Path, path: &Path) -> String {
    let resolved = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    if let Ok(inside) = resolved.strip_prefix(root) {
        return posix_parts(inside).join("/");
    }
    let shared = resolved
        .components()
        .zip(root.components())
        .take_while(|(left, right)| left == right)
        .count();
    if shared == 0 {
        // A different Windows drive has no relative path.
        let absolute = resolved.to_string_lossy().replace('\\', "/");
        return absolute
            .strip_prefix("//?/")
            .unwrap_or(&absolute)
            .to_owned();
    }
    let ups = root.components().count() - shared;
    let mut parts = vec!["..".to_owned(); ups];
    parts.extend(posix_parts(
        &resolved.components().skip(shared).collect::<PathBuf>(),
    ));
    parts.join("/")
}

fn posix_parts(path: &Path) -> Vec<String> {
    path.components()
        .filter_map(|component| match component {
            Component::Normal(part) => Some(part.to_string_lossy().into_owned()),
            _ => None,
        })
        .collect()
}

/// Python files below a directory, skipping tool directories and symlink cycles.
pub fn python_files(directory: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut visited = HashSet::new();
    let mut pending = vec![directory.to_path_buf()];
    while let Some(parent) = pending.pop() {
        let resolved = fs::canonicalize(&parent).unwrap_or_else(|_| parent.clone());
        if !visited.insert(resolved) {
            continue;
        }
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
                found.push(path);
            }
        }
    }
    found
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
