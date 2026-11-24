//! Static re-exports of installed third-party packages, read without importing them.
//!
//! The Python engine read the search path of the interpreter it ran in. The native
//! core has none, so it looks in the environment that holds its own executable, the
//! active `VIRTUAL_ENV` and the project's `.venv`, in that order.
use super::imports::is_type_checking;
use super::namespace::resolve_relative;
use super::walk::{TreeNode, expression_key, tree_nodes};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use ruff_python_ast::{Expr, ExprContext, ModModule, Operator, Stmt};
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const SUFFIXES: [&str; 2] = [".pyi", ".py"];

/// Public name to imported name.
pub type Exports = HashMap<String, String>;

/// Installed package sources on an import search path, parsed on first use.
///
/// The first search-path entry holding a regular package wins, as for imports.
/// Stubs take precedence over sources. Namespace packages are not resolved.
pub struct InstalledPackages {
    project_root: PathBuf,
    search_path: Option<Vec<PathBuf>>,
    roots: HashMap<String, Option<PathBuf>>,
    exports: HashMap<String, Exports>,
}

impl InstalledPackages {
    pub fn new(project_root: &Path) -> Self {
        Self {
            project_root: project_root.to_path_buf(),
            search_path: None,
            roots: HashMap::new(),
            exports: HashMap::new(),
        }
    }

    /// Public name to imported name for one installed package initializer.
    pub fn exports(&mut self, package: &str) -> Exports {
        if let Some(found) = self.exports.get(package) {
            return found.clone();
        }
        let mut result = Exports::new();
        if let Some((path, true)) = self.source(package)
            && let Some(parsed) = parse(&path)
        {
            let stub = path.extension().is_some_and(|suffix| suffix == "pyi");
            result = self.initializer_exports(package, parsed.module(), stub);
        }
        self.exports.insert(package.to_owned(), result.clone());
        result
    }

    fn initializer_exports(&mut self, package: &str, tree: &ModModule, stub: bool) -> Exports {
        let declares_all = declares_all(tree);
        let exports = static_all(tree);
        if declares_all && exports.is_none() {
            return Exports::new();
        }
        let definitions = definition_names(tree);
        // A lazy __getattr__ serves names that only type checkers import.
        let lazy = definitions.contains("__getattr__") && declares_all;
        let mut bindings: HashMap<&str, usize> = HashMap::new();
        for node in tree_nodes(tree) {
            let aliases = match node {
                TreeNode::Statement(Stmt::Import(item)) => &item.names,
                TreeNode::Statement(Stmt::ImportFrom(item)) => &item.names,
                _ => continue,
            };
            for alias in aliases {
                if alias.name.as_str() != "*" {
                    let name = match &alias.asname {
                        Some(name) => name.as_str(),
                        None => alias.name.split('.').next().unwrap_or_default(),
                    };
                    *bindings.entry(name).or_insert(0) += 1;
                }
            }
        }
        let top = package.split('.').next().unwrap_or(package);
        let mut candidates: HashMap<String, Vec<String>> = HashMap::new();
        for (statement, guarded) in module_statements(tree) {
            let Stmt::ImportFrom(node) = statement else {
                continue;
            };
            if guarded && !lazy {
                continue;
            }
            let base = resolve_relative(
                package,
                true,
                node.level,
                node.module.as_ref().map(|name| name.as_str()),
            );
            // A re-export of another distribution never shortens an import of this one.
            if base.split('.').next() != Some(top) {
                continue;
            }
            for alias in &node.names {
                if alias.name.as_str() == "*" {
                    for name in self.star_names(&base) {
                        candidates
                            .entry(name.clone())
                            .or_default()
                            .push(format!("{base}.{name}"));
                    }
                } else if !stub
                    || declares_all
                    || alias
                        .asname
                        .as_ref()
                        .is_some_and(|name| *name == alias.name)
                {
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                    candidates
                        .entry(name)
                        .or_default()
                        .push(format!("{base}.{}", alias.name));
                }
            }
        }
        candidates
            .into_iter()
            .filter(|(name, targets)| {
                targets.len() == 1
                    && bindings.get(name.as_str()).copied().unwrap_or(0) <= 1
                    && !name.starts_with('_')
                    && !definitions.contains(name.as_str())
                    && exports.as_ref().is_none_or(|listed| listed.contains(name))
            })
            .map(|(name, mut targets)| (name, targets.swap_remove(0)))
            .collect()
    }

    /// Names a star import of `module` binds, known only from a static `__all__`.
    fn star_names(&mut self, module: &str) -> HashSet<String> {
        self.source(module)
            .and_then(|(path, _)| parse(&path))
            .and_then(|parsed| static_all(parsed.module()))
            .unwrap_or_default()
    }

    /// The stub or source of `module` and whether it is a package.
    fn source(&mut self, module: &str) -> Option<(PathBuf, bool)> {
        let mut parts = module.split('.');
        let top = parts.next()?;
        let rest: Vec<&str> = parts.collect();
        let mut directory = self.root(top)?;
        for part in &rest {
            directory.push(part);
        }
        for suffix in SUFFIXES {
            let initializer = directory.join(format!("__init__{suffix}"));
            if initializer.is_file() {
                return Some((initializer, true));
            }
        }
        let last = rest.last()?;
        for suffix in SUFFIXES {
            let path = directory.with_file_name(format!("{last}{suffix}"));
            if path.is_file() {
                return Some((path, false));
            }
        }
        None
    }

    fn root(&mut self, top: &str) -> Option<PathBuf> {
        if let Some(found) = self.roots.get(top) {
            return found.clone();
        }
        let project_root = self.project_root.clone();
        let search_path = self
            .search_path
            .get_or_insert_with(|| search_path(&project_root));
        let found = search_path
            .iter()
            .map(|entry| entry.join(top))
            .find(|directory| {
                SUFFIXES
                    .iter()
                    .any(|suffix| directory.join(format!("__init__{suffix}")).is_file())
            });
        self.roots.insert(top.to_owned(), found.clone());
        found
    }
}

fn parse(path: &Path) -> Option<ParsedSource> {
    let source = SourceFile {
        module: String::new(),
        path: path.display().to_string(),
        absolute: path.to_path_buf(),
        is_package: false,
    };
    ParsedSource::read(&source).ok()
}

/// Whether any name `__all__` appears in the module.
pub fn declares_all(tree: &ModModule) -> bool {
    tree_nodes(tree).into_iter().any(
        |node| matches!(node, TreeNode::Expression(Expr::Name(name)) if name.id.as_str() == "__all__"),
    )
}

/// Names bound by assignment, deletion or definition anywhere in the module.
pub fn definition_names(tree: &ModModule) -> HashSet<&str> {
    let mut names = HashSet::new();
    for node in tree_nodes(tree) {
        match node {
            TreeNode::Expression(Expr::Name(name)) if name.ctx != ExprContext::Load => {
                names.insert(name.id.as_str());
            }
            TreeNode::Statement(Stmt::FunctionDef(item)) => {
                names.insert(item.name.as_str());
            }
            TreeNode::Statement(Stmt::ClassDef(item)) => {
                names.insert(item.name.as_str());
            }
            _ => {}
        }
    }
    names
}

/// Literal top-level `__all__` assignments; `None` when absent or built dynamically.
///
/// Reading `__all__`, as a module `__dir__` does, leaves it static.
fn static_all(tree: &ModModule) -> Option<HashSet<String>> {
    let mut names = HashSet::new();
    let mut assigned = HashSet::new();
    for statement in &tree.body {
        let (targets, value): (&[Expr], Option<&Expr>) = match statement {
            Stmt::Assign(node) => (&node.targets, Some(&node.value)),
            Stmt::AnnAssign(node) => (
                std::slice::from_ref(node.target.as_ref()),
                node.value.as_deref(),
            ),
            Stmt::AugAssign(node) => (
                std::slice::from_ref(node.target.as_ref()),
                Some(&node.value),
            ),
            _ => continue,
        };
        let hits: Vec<&Expr> = targets
            .iter()
            .filter(|target| matches!(target, Expr::Name(name) if name.id.as_str() == "__all__"))
            .collect();
        if hits.is_empty() {
            continue;
        }
        let items = match value? {
            Expr::List(node) => &node.elts,
            Expr::Tuple(node) => &node.elts,
            Expr::Set(node) => &node.elts,
            _ => return None,
        };
        if matches!(statement, Stmt::AugAssign(node) if node.op != Operator::Add) {
            return None;
        }
        let literals = items
            .iter()
            .map(|item| match item {
                Expr::StringLiteral(text) => Some(text.value.to_str().to_owned()),
                _ => None,
            })
            .collect::<Option<Vec<_>>>()?;
        if !matches!(statement, Stmt::AugAssign(_)) {
            names.clear();
        }
        names.extend(literals);
        assigned.extend(hits.into_iter().map(expression_key));
    }
    for node in tree_nodes(tree) {
        let TreeNode::Expression(expression) = node else {
            continue;
        };
        let dynamic = match expression {
            Expr::Name(name) => {
                name.id.as_str() == "__all__"
                    && name.ctx != ExprContext::Load
                    && !assigned.contains(&expression_key(expression))
            }
            Expr::Attribute(attribute) => {
                matches!(attribute.value.as_ref(), Expr::Name(name) if name.id.as_str() == "__all__")
            }
            _ => false,
        };
        if dynamic {
            return None;
        }
    }
    (!assigned.is_empty()).then_some(names)
}

/// Top-level statements, plus those under `if TYPE_CHECKING:` marked guarded.
fn module_statements(tree: &ModModule) -> Vec<(&Stmt, bool)> {
    let mut statements = Vec::new();
    for node in &tree.body {
        match node {
            Stmt::If(item) if is_type_checking(&item.test) => {
                statements.extend(item.body.iter().map(|child| (child, true)));
            }
            other => statements.push((other, false)),
        }
    }
    statements
}

/// The directories an interpreter of the project's environments would import from.
fn search_path(project_root: &Path) -> Vec<PathBuf> {
    let mut prefixes = Vec::new();
    if let Ok(executable) = env::current_exe()
        && let Some(prefix) = executable.parent().and_then(Path::parent)
    {
        prefixes.push(prefix.to_path_buf());
    }
    if let Some(active) = env::var_os("VIRTUAL_ENV").filter(|value| !value.is_empty()) {
        prefixes.push(PathBuf::from(active));
    }
    prefixes.push(project_root.join(".venv"));
    let mut entries = Vec::new();
    for prefix in prefixes {
        environment_entries(&prefix, &mut entries);
    }
    let mut seen = HashSet::new();
    entries.retain(|entry| seen.insert(entry.clone()));
    entries
}

fn environment_entries(prefix: &Path, entries: &mut Vec<PathBuf>) {
    let sites = site_packages(prefix);
    if sites.is_empty() {
        return;
    }
    let config = fs::read_to_string(prefix.join("pyvenv.cfg"))
        .map(|text| venv_config(&text))
        .unwrap_or_default();
    let base = config.get("home").map(PathBuf::from);
    // The standard library comes first on sys.path.
    match &base {
        Some(home) => {
            entries.extend(standard_library(home));
            if let Some(parent) = home.parent() {
                entries.extend(standard_library(parent));
            }
        }
        None => entries.extend(standard_library(prefix)),
    }
    for site in sites {
        entries.push(site.clone());
        entries.extend(path_entries(&site));
    }
    let system_site = config
        .get("include-system-site-packages")
        .is_some_and(|value| value.eq_ignore_ascii_case("true"));
    if system_site && let Some(home) = &base {
        for candidate in [Some(home.as_path()), home.parent()].into_iter().flatten() {
            for site in site_packages(candidate) {
                entries.push(site.clone());
                entries.extend(path_entries(&site));
            }
        }
    }
}

fn venv_config(text: &str) -> HashMap<String, String> {
    text.lines()
        .filter_map(|line| line.split_once('='))
        .map(|(key, value)| (key.trim().to_lowercase(), value.trim().to_owned()))
        .collect()
}

/// `Lib/site-packages` on Windows, `lib/python3.X/site-packages` elsewhere.
fn site_packages(prefix: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let windows = prefix.join("Lib").join("site-packages");
    if windows.is_dir() {
        found.push(windows);
    }
    for library in versioned_libraries(prefix) {
        let site = library.join("site-packages");
        if site.is_dir() && !found.contains(&site) {
            found.push(site);
        }
    }
    found
}

fn standard_library(prefix: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    let windows = prefix.join("Lib");
    if windows.join("os.py").is_file() {
        found.push(windows);
    }
    found.extend(
        versioned_libraries(prefix)
            .into_iter()
            .filter(|library| library.join("os.py").is_file()),
    );
    found
}

fn versioned_libraries(prefix: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    for library in ["lib", "lib64"] {
        let Ok(entries) = fs::read_dir(prefix.join(library)) else {
            continue;
        };
        let mut versions: Vec<PathBuf> = entries
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().starts_with("python3"))
            .map(|entry| entry.path())
            .collect();
        versions.sort();
        found.extend(versions);
    }
    found
}

/// The directories `.pth` files in `site` add, as `site.addpackage` reads them.
fn path_entries(site: &Path) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(site) else {
        return Vec::new();
    };
    let mut files: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|suffix| suffix == "pth"))
        .collect();
    files.sort();
    let mut found = Vec::new();
    for file in files {
        let Ok(text) = fs::read_to_string(&file) else {
            continue;
        };
        for line in text.lines() {
            if line.starts_with('#') || line.starts_with("import ") || line.starts_with("import\t")
            {
                continue;
            }
            let line = line.trim_end();
            if line.is_empty() {
                continue;
            }
            let directory = site.join(line);
            if directory.exists() {
                found.push(directory);
            }
        }
    }
    found
}
