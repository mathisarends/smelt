//! Bounded symbol provenance and runtime demand for package facades, like
//! `smelt.analysis.facade_usage`.
use super::imports::type_checking_lines;
use super::lazy_exports::static_lazy_exports;
use super::members::definition_counts;
use super::namespace::resolve_relative;
use crate::checker::files::{FileIndex, SourceFile};
use crate::checker::patterns::is_within;
use crate::checker::rules::explicit_exports;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::Ranged;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};

// Re-export chains are short; a longer one is more likely a cycle than a design.
const MAX_HOPS: usize = 8;
const DYNAMIC_CALLS: [&str; 5] = ["exec", "eval", "globals", "locals", "vars"];

/// The single top-level definition a public name resolves to.
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct Definition {
    pub module: String,
    pub name: String,
    pub path: String,
    pub line: usize,
    pub column: usize,
}

/// A public implementation name that outside modules import past its package facade.
pub struct FacadeGap {
    pub module: String,
    pub name: String,
    pub path: String,
    pub line: usize,
    pub column: usize,
    pub source_importers: Vec<String>,
    pub test_importers: Vec<String>,
    /// Other public paths that already export the definition.
    pub reexports: Vec<String>,
    /// A runtime import path from the facade back to itself through the definition.
    pub cycle: Vec<String>,
    /// Whether the facade lacks the import, rather than only its `__all__` entry.
    pub needs_import: bool,
}

/// Facade inputs of one module.
#[derive(Default)]
struct ModuleFacts {
    module: String,
    opaque: bool,
    /// Name to the position of its single top-level definition.
    definitions: HashMap<String, (usize, usize)>,
    /// Name to the qualified name it statically re-exports.
    reexports: HashMap<String, String>,
    bound: HashSet<String>,
    public: Option<HashSet<String>>,
    declares_all: bool,
    /// Runtime `from base import names` statements, without private names.
    imports: Vec<(String, Vec<String>)>,
}

struct Use {
    definition: Definition,
    importer: String,
    test: bool,
}

/// Facade demand shared by the export, fan-out and ownership checks of one run.
pub struct FacadeUsage<'a> {
    files: &'a FileIndex,
    facts: HashMap<&'a str, ModuleFacts>,
    modules: HashSet<&'a str>,
    exports: HashMap<Definition, BTreeSet<String>>,
    uses: Vec<Use>,
    adjacency: HashMap<&'a str, BTreeSet<&'a str>>,
}

impl<'a> FacadeUsage<'a> {
    /// `adjacency` is the explicit runtime graph between first-party modules.
    pub fn new(
        files: &'a FileIndex,
        sources: &[(&'a SourceFile, &'a ParsedSource)],
        tests: &[(&'a SourceFile, &'a ParsedSource)],
        test_roots: &[String],
        adjacency: HashMap<&'a str, BTreeSet<&'a str>>,
    ) -> Self {
        let facts: HashMap<&str, ModuleFacts> = sources
            .par_iter()
            .chain(tests.par_iter())
            .map(|(file, parsed)| (file.path.as_str(), module_facts(file, parsed)))
            .collect();
        let modules = files
            .sources
            .keys()
            .chain(&files.packages)
            .map(String::as_str)
            .collect();
        let mut usage = Self {
            files,
            facts,
            modules,
            exports: HashMap::new(),
            uses: Vec::new(),
            adjacency,
        };
        usage.exports = usage.collect_exports();
        let mut paths: Vec<(&str, bool)> = files
            .sources
            .values()
            .map(|source| {
                (
                    source.path.as_str(),
                    files.is_test_path(&source.path, test_roots),
                )
            })
            .collect();
        paths.extend(files.tests.keys().map(|path| (path.as_str(), true)));
        usage.uses = usage.collect_uses(&paths);
        usage
    }

    /// Verify explicit ownership decisions against identity and the static exports.
    ///
    /// Returns the verified definitions with their owners, and the declarations
    /// that failed with why.
    pub fn canonical_exports(
        &self,
        declarations: &BTreeMap<String, String>,
    ) -> (HashMap<String, String>, Vec<(String, String)>) {
        let mut verified = HashMap::new();
        let mut issues = Vec::new();
        let mut owners: HashMap<Definition, &str> = HashMap::new();
        for (symbol, export) in declarations {
            let (module, name) = rpartition(symbol);
            let definition = self.resolve(module, name);
            let (owner, public_name) = rpartition(export);
            let target = self.resolve(owner, public_name);
            let Some(definition) = definition else {
                issues.push((
                    symbol.clone(),
                    format!(
                        "cannot statically resolve {symbol}; use an analyzed, unambiguous \
                         definition or static re-export"
                    ),
                ));
                continue;
            };
            if target.as_ref() != Some(&definition)
                || !self
                    .exports
                    .get(&definition)
                    .is_some_and(|exports| exports.contains(export))
            {
                issues.push((
                    symbol.clone(),
                    format!(
                        "{export} is not a verified public export of {symbol}; declare a static \
                         re-export and include it in __all__ when declared. Unsupported dynamic \
                         exports cannot verify ownership."
                    ),
                ));
            } else if let Some(owner) = owners.get(&definition)
                && *owner != export.as_str()
            {
                issues.push((
                    symbol.clone(),
                    format!(
                        "conflicting canonical exports for {}.{}: {owner} and {export}; choose \
                         one public owner",
                        definition.module, definition.name
                    ),
                ));
            } else {
                verified.insert(
                    format!("{}.{}", definition.module, definition.name),
                    export.clone(),
                );
                owners.insert(definition, export);
            }
        }
        (verified, issues)
    }

    /// Public names of `package`'s modules that outside callers import past its facade.
    pub fn gaps(&self, package: &str, count_tests: bool) -> Vec<FacadeGap> {
        let Some(source) = self.files.sources.get(package) else {
            return Vec::new();
        };
        if !source.is_package {
            return Vec::new();
        }
        let Some(facade) = self.facts.get(source.path.as_str()) else {
            return Vec::new();
        };
        let Some(exports) = facade.public.as_ref().filter(|_| !facade.opaque) else {
            return Vec::new();
        };
        let mut callers: HashMap<&Definition, (BTreeSet<&str>, BTreeSet<&str>)> = HashMap::new();
        for used in &self.uses {
            let definition = &used.definition;
            if (used.test && !count_tests)
                || definition.module == package
                || !is_within(&definition.module, package)
                || (is_within(&used.importer, package) && !used.test)
            {
                continue;
            }
            let (sources, tests) = callers.entry(definition).or_default();
            if used.test {
                tests.insert(&used.importer);
            } else {
                sources.insert(&used.importer);
            }
        }
        let mut callers: Vec<_> = callers.into_iter().collect();
        callers.sort_by(|(left, _), (right, _)| {
            (&left.module, &left.name).cmp(&(&right.module, &right.name))
        });
        let mut gaps = Vec::new();
        for (definition, (sources, tests)) in callers {
            let name = &definition.name;
            let existing = self.resolve(package, name);
            let exported = existing.as_ref() == Some(definition);
            if exported && (!facade.declares_all || exports.contains(name)) {
                continue;
            }
            // A conflicting or uncertain binding must never suggest overwriting an API.
            if !exported && (facade.bound.contains(name) || exports.contains(name)) {
                continue;
            }
            let own = format!("{package}.{name}");
            gaps.push(FacadeGap {
                module: definition.module.clone(),
                name: name.clone(),
                path: definition.path.clone(),
                line: definition.line,
                column: definition.column,
                source_importers: sources.into_iter().map(str::to_owned).collect(),
                test_importers: tests.into_iter().map(str::to_owned).collect(),
                reexports: self
                    .exports
                    .get(definition)
                    .into_iter()
                    .flatten()
                    .filter(|export| **export != own)
                    .cloned()
                    .collect(),
                cycle: self.cycle(package, &definition.module),
                needs_import: !exported,
            });
        }
        gaps
    }

    fn collect_uses(&self, paths: &[(&str, bool)]) -> Vec<Use> {
        let mut uses = Vec::new();
        for &(path, test) in paths {
            let Some(facts) = self.facts.get(path) else {
                continue;
            };
            for (base, names) in &facts.imports {
                // Published facade imports do not represent deep coupling.
                if self
                    .files
                    .sources
                    .get(base)
                    .is_none_or(|source| source.is_package)
                {
                    continue;
                }
                for name in names {
                    if let Some(definition) = self.resolve(base, name)
                        && !definition.name.starts_with('_')
                    {
                        uses.push(Use {
                            definition,
                            importer: facts.module.clone(),
                            test,
                        });
                    }
                }
            }
        }
        uses
    }

    fn collect_exports(&self) -> HashMap<Definition, BTreeSet<String>> {
        let mut result: HashMap<Definition, BTreeSet<String>> = HashMap::new();
        for source in self.files.sources.values() {
            let Some(facts) = self.facts.get(source.path.as_str()) else {
                continue;
            };
            if facts.opaque {
                continue;
            }
            let Some(exports) = &facts.public else {
                continue;
            };
            if !source.is_package && !facts.declares_all {
                continue;
            }
            for name in facts.definitions.keys().chain(facts.reexports.keys()) {
                if name.starts_with('_') || (facts.declares_all && !exports.contains(name)) {
                    continue;
                }
                if let Some(definition) = self.resolve(&source.module, name) {
                    result
                        .entry(definition)
                        .or_default()
                        .insert(format!("{}.{name}", source.module));
                }
            }
        }
        result
    }

    /// Follow static re-exports of `module.name` to its single definition.
    fn resolve(&self, module: &str, name: &str) -> Option<Definition> {
        let mut seen: HashSet<(String, String)> = HashSet::new();
        let (mut module, mut name) = (module.to_owned(), name.to_owned());
        while seen.len() < MAX_HOPS && seen.insert((module.clone(), name.clone())) {
            let source = self.files.sources.get(&module)?;
            let facts = self.facts.get(source.path.as_str())?;
            if facts.opaque {
                return None;
            }
            if let Some(&(line, column)) = facts.definitions.get(&name) {
                return Some(Definition {
                    module,
                    name,
                    path: source.path.clone(),
                    line,
                    column,
                });
            }
            let target = facts.reexports.get(&name)?;
            if self.modules.contains(target.as_str()) {
                return None; // a module, rather than a symbol
            }
            let (parent, attribute) = rpartition(target);
            (module, name) = (parent.to_owned(), attribute.to_owned());
        }
        None
    }

    /// The shortest runtime path `package -> module -> ... -> package`, if any.
    fn cycle(&self, package: &str, module: &str) -> Vec<String> {
        let mut previous: HashMap<&str, Option<&str>> = HashMap::from([(module, None)]);
        let mut queue = VecDeque::from([module]);
        while let Some(current) = queue.pop_front() {
            for &child in self.adjacency.get(current).into_iter().flatten() {
                if previous.contains_key(child) {
                    continue;
                }
                previous.insert(child, Some(current));
                if child == package {
                    let mut chain = vec![package];
                    while let Some(parent) = chain.last().and_then(|last| previous[last]) {
                        chain.push(parent);
                    }
                    return std::iter::once(package)
                        .chain(chain.into_iter().rev())
                        .map(str::to_owned)
                        .collect();
                }
                queue.push_back(child);
            }
        }
        Vec::new()
    }
}

/// `str.rpartition('.')` without the separator.
fn rpartition(qualified: &str) -> (&str, &str) {
    qualified.rsplit_once('.').unwrap_or(("", qualified))
}

fn module_facts(file: &SourceFile, parsed: &ParsedSource) -> ModuleFacts {
    let tree = parsed.module();
    let mut facts = static_bindings(file, parsed);
    let names = explicit_exports(tree);
    let lazy = static_lazy_exports(&parsed.text.text, tree);
    facts.public = match (names, lazy) {
        (None, Some(lazy)) => Some(lazy.into_keys().collect()),
        (names, _) => names.map(|names| names.into_iter().map(str::to_owned).collect()),
    };
    let mut scan = Scan::default();
    scan.visit_body(&tree.body);
    facts.declares_all = scan.declares_all;
    let guarded = type_checking_lines(parsed);
    facts.imports = scan
        .from_imports
        .iter()
        .filter(|node| !guarded.contains(&parsed.text.line_at(usize::from(node.start()))))
        .map(|node| {
            let base = resolve_relative(
                &file.module,
                file.is_package,
                node.level,
                node.module.as_ref().map(|name| name.as_str()),
            );
            let names = node
                .names
                .iter()
                .map(|alias| alias.name.as_str())
                .filter(|name| !name.starts_with('_') && *name != "*")
                .map(str::to_owned)
                .collect();
            (base, names)
        })
        .collect();
    facts
}

fn static_bindings(file: &SourceFile, parsed: &ParsedSource) -> ModuleFacts {
    let tree = parsed.module();
    let lazy = static_lazy_exports(&parsed.text.text, tree);
    let definitions = definition_counts(&tree.body);
    let mut scan = Scan::default();
    scan.visit_body(&tree.body);
    let opaque = ModuleFacts {
        module: file.module.clone(),
        opaque: true,
        ..ModuleFacts::default()
    };
    if (scan.dynamic && lazy.is_none()) || scan.star {
        return opaque;
    }
    let mut imports: HashMap<&str, usize> = HashMap::new();
    for name in &scan.import_names {
        *imports.entry(name).or_default() += 1;
    }
    let mut definitions_at: HashMap<String, (usize, usize)> = HashMap::new();
    let mut reexports: HashMap<String, String> = HashMap::new();
    for statement in &tree.body {
        if let Stmt::ImportFrom(node) = statement {
            let base = resolve_relative(
                &file.module,
                file.is_package,
                node.level,
                node.module.as_ref().map(|name| name.as_str()),
            );
            for alias in &node.names {
                let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                if !definitions.contains_key(name) && imports.get(name) == Some(&1) {
                    definitions_at.remove(name);
                    reexports.insert(name.to_owned(), format!("{base}.{}", alias.name));
                }
            }
            continue;
        }
        for name in definition_names(statement) {
            if definitions.get(name) == Some(&1) && !imports.contains_key(name) {
                let start = parsed.statement_start(statement);
                let (line, column) = parsed.text.position(usize::from(start));
                reexports.remove(name);
                definitions_at.insert(name.to_owned(), (line, column + 1));
            }
        }
    }
    let mut bound: HashSet<String> = definitions.keys().map(|name| (*name).to_owned()).collect();
    bound.extend(imports.keys().map(|name| (*name).to_owned()));
    let opaque_handler = definitions.contains_key("__getattr__") && lazy.is_none();
    if let Some(lazy) = lazy {
        for (name, target) in lazy {
            bound.insert(name.clone());
            if !definitions_at.contains_key(&name)
                && !reexports.contains_key(&name)
                && !definitions.contains_key(name.as_str())
                && !imports.contains_key(name.as_str())
            {
                reexports.insert(name, target);
            }
        }
    }
    ModuleFacts {
        module: file.module.clone(),
        opaque: opaque_handler,
        definitions: definitions_at,
        reexports,
        bound,
        ..ModuleFacts::default()
    }
}

fn definition_names(statement: &Stmt) -> Vec<&str> {
    match statement {
        Stmt::FunctionDef(node) => vec![node.name.as_str()],
        Stmt::ClassDef(node) => vec![node.name.as_str()],
        Stmt::Assign(node) => node
            .targets
            .iter()
            .filter_map(|target| match target {
                Expr::Name(name) => Some(name.id.as_str()),
                _ => None,
            })
            .collect(),
        Stmt::AnnAssign(node) if node.value.is_some() => match node.target.as_ref() {
            Expr::Name(name) => vec![name.id.as_str()],
            _ => Vec::new(),
        },
        Stmt::TypeAlias(node) => match node.name.as_ref() {
            Expr::Name(name) => vec![name.id.as_str()],
            _ => Vec::new(),
        },
        _ => Vec::new(),
    }
}

/// Whole-module facts: imports anywhere, dynamic namespace calls, `__all__` uses.
#[derive(Default)]
struct Scan<'a> {
    from_imports: Vec<&'a ruff_python_ast::StmtImportFrom>,
    import_names: Vec<&'a str>,
    star: bool,
    dynamic: bool,
    declares_all: bool,
}

impl<'a> Visitor<'a> for Scan<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    self.star |= alias.name.as_str() == "*";
                    self.import_names.push(match &alias.asname {
                        Some(name) => name.as_str(),
                        None => alias.name.split('.').next().unwrap_or_default(),
                    });
                }
            }
            Stmt::ImportFrom(node) => {
                self.from_imports.push(node);
                for alias in &node.names {
                    self.star |= alias.name.as_str() == "*";
                    self.import_names
                        .push(alias.asname.as_ref().unwrap_or(&alias.name).as_str());
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Call(call) => {
                if let Expr::Name(name) = call.func.as_ref() {
                    self.dynamic |= DYNAMIC_CALLS.contains(&name.id.as_str());
                }
            }
            Expr::Name(name) => self.declares_all |= name.id.as_str() == "__all__",
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
}
