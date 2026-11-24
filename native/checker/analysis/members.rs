//! Conservative use evidence for module functions and direct class helpers, like
//! `smelt.analysis.members.MemberUsageIndex` without its cache.
use super::bindings::{Names, import_bindings};
use super::namespace::{Namespace, resolve_dotted, resolve_export, resolve_relative};
use super::nodes::{names_in, walk_expr_in_field_order, walk_statements, walk_stmt_in_field_order};
use super::test_syntax::TestModule;
use crate::checker::config::Project;
use crate::checker::files::SourceFile;
use crate::checker::rules::explicit_exports;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprContext, ModModule, Stmt, StmtClassDef, StmtFunctionDef};
use ruff_text_size::{Ranged, TextSize};
use serde_json::{Value, json};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::Arc;

const REFLECTION: [&str; 4] = ["getattr", "setattr", "hasattr", "delattr"];
const REFLECTION_ARGUMENTS: usize = 2;
const STATICMETHOD: [&str; 2] = ["staticmethod", "builtins.staticmethod"];
const OBJECT: [&str; 2] = ["object", "builtins.object"];
// Slots and custom attribute hooks make renaming a field a runtime contract.
const ATTRIBUTE_HOOKS: [&str; 5] = [
    "__slots__",
    "__getattribute__",
    "__getattr__",
    "__setattr__",
    "__delattr__",
];

type Target = (String, Option<String>);

/// A definition's name and its 1-based line and column.
#[derive(Clone)]
pub struct Definition {
    pub name: String,
    pub line: usize,
    pub column: usize,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum MemberKind {
    Function,
    Method,
    StaticMethod,
}

impl MemberKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Function => "function",
            Self::Method => "method",
            Self::StaticMethod => "staticmethod",
        }
    }
}

#[derive(Clone)]
pub struct Member<'a> {
    pub source: &'a SourceFile,
    pub owner: Option<Definition>,
    pub node: Definition,
    pub kind: MemberKind,
    pub internal_references: Vec<usize>,
    pub external_use: bool,
    pub production_external_use: bool,
    pub class_dependencies: bool,
    pub occupied_names: Arc<HashSet<String>>,
    pub module_names: Arc<HashSet<String>>,
}

pub struct AttributeMember<'a> {
    pub source: &'a SourceFile,
    pub owner: Definition,
    pub node: Definition,
    pub name: String,
    pub kind: &'static str,
    pub internal_references: Vec<usize>,
    pub occupied_names: HashSet<String>,
}

#[derive(Clone, Copy)]
struct AttributeLine {
    line: usize,
    /// The line of the class whose method makes the access, if any.
    owner: Option<usize>,
    internal: bool,
}

struct FileFacts<'a> {
    source: &'a SourceFile,
    production: bool,
    candidates: Vec<Member<'a>>,
    imports: BTreeSet<String>,
    loads: BTreeSet<String>,
    stars: BTreeSet<String>,
    attributes: HashMap<String, Vec<AttributeLine>>,
    bases: Vec<String>,
    attribute_candidates: Vec<AttributeMember<'a>>,
    public_exports: Vec<String>,
}

struct Resolved {
    targets: Vec<Target>,
    inherited: Vec<Target>,
    public_exports: Vec<Target>,
}

pub struct MemberUsageIndex<'a> {
    namespaces: HashMap<&'a str, Namespace>,
    files: Vec<FileFacts<'a>>,
    module_uses: HashMap<Target, HashSet<&'a str>>,
    production_uses: HashMap<Target, HashSet<&'a str>>,
    /// Every same-named attribute access, and those in production files.
    attribute_counts: HashMap<String, (usize, usize)>,
    inherited: HashSet<Target>,
    public_exports: HashSet<Target>,
    members: Vec<Member<'a>>,
}

impl<'a> MemberUsageIndex<'a> {
    pub fn new(
        sources: &[(&'a SourceFile, &'a ParsedSource)],
        tests: &[(&'a SourceFile, &'a ParsedSource)],
        namespaces: HashMap<&'a str, Namespace>,
    ) -> Self {
        let mut inputs: Vec<(&SourceFile, &ParsedSource, bool)> = sources
            .iter()
            .map(|&(source, parsed)| (source, parsed, true))
            .chain(tests.iter().map(|&(test, parsed)| (test, parsed, false)))
            .collect();
        inputs.sort_by(|left, right| left.0.path.cmp(&right.0.path));
        let files: Vec<FileFacts> = inputs
            .par_iter()
            .map(|&(source, parsed, production)| {
                file_facts(source, parsed, production, &namespaces)
            })
            .collect();
        let resolved: Vec<Resolved> = files
            .par_iter()
            .map(|file| resolve_references(file, &namespaces))
            .collect();
        let mut index = Self {
            namespaces,
            files: Vec::new(),
            module_uses: HashMap::new(),
            production_uses: HashMap::new(),
            attribute_counts: HashMap::new(),
            inherited: HashSet::new(),
            public_exports: HashSet::new(),
            members: Vec::new(),
        };
        for (file, references) in files.iter().zip(resolved) {
            let module = file.source.module.as_str();
            for target in references.targets {
                if file.production {
                    index
                        .production_uses
                        .entry(target.clone())
                        .or_default()
                        .insert(module);
                }
                index.module_uses.entry(target).or_default().insert(module);
            }
            for (name, uses) in &file.attributes {
                let counts = index.attribute_counts.entry(name.clone()).or_default();
                counts.0 += uses.len();
                if file.production {
                    counts.1 += uses.len();
                }
            }
            index.inherited.extend(references.inherited);
            index.public_exports.extend(references.public_exports);
        }
        index.files = files;
        index.members = index.resolve_members();
        index
    }

    /// What the analysis could see, so absent callers are not read as a proof.
    pub fn usage_scope(project: &Project) -> Value {
        json!({
            "root_packages": project.root_packages,
            "source_roots": project.source_roots,
            "test_roots": project.test_roots,
            "excluded_paths": project.exclude,
            "unassessed_consumers": [
                "excluded files (including maintainer scripts)",
                "files outside selected packages and roots",
                "external consumers and arbitrary dynamic access",
            ],
        })
    }

    pub fn members(&self) -> &[Member<'a>] {
        &self.members
    }

    fn resolve_members(&self) -> Vec<Member<'a>> {
        let mut members = Vec::new();
        for file in &self.files {
            let module = file.source.module.as_str();
            for candidate in &file.candidates {
                let mut member = candidate.clone();
                if let Some(owner) = &candidate.owner {
                    let key = (module.to_owned(), Some(owner.name.clone()));
                    if self.inherited.contains(&key)
                        || (!candidate.node.name.starts_with('_')
                            && self.public_exports.contains(&key))
                    {
                        continue;
                    }
                    let local: Vec<usize> = file
                        .attributes
                        .get(&candidate.node.name)
                        .into_iter()
                        .flatten()
                        .filter(|use_| use_.owner == Some(owner.line) && use_.internal)
                        .map(|use_| use_.line)
                        .collect();
                    let (total, production) = self.counts(&candidate.node.name);
                    member.external_use = total > local.len();
                    member.production_external_use = production > local.len();
                    member.internal_references = local
                        .into_iter()
                        .collect::<BTreeSet<_>>()
                        .into_iter()
                        .collect();
                } else {
                    member.external_use =
                        self.function_external(module, &candidate.node.name, false);
                    member.production_external_use =
                        self.function_external(module, &candidate.node.name, true);
                }
                members.push(member);
            }
        }
        members
    }

    fn counts(&self, name: &str) -> (usize, usize) {
        self.attribute_counts.get(name).copied().unwrap_or_default()
    }

    /// Imports, re-exports, tests and escaped module values all count as use.
    pub fn module_name_used_externally(&self, module: &str, name: &str) -> bool {
        self.function_external(module, name, false)
    }

    fn function_external(&self, module: &str, name: &str, production: bool) -> bool {
        let uses = if production {
            &self.production_uses
        } else {
            &self.module_uses
        };
        let parts: Vec<&str> = module.split('.').collect();
        let targets = std::iter::once((module.to_owned(), Some(name.to_owned())))
            .chain((1..=parts.len()).map(|end| (parts[..end].join("."), None)));
        targets.into_iter().any(|target| {
            uses.get(&target).is_some_and(|users| {
                !users.is_empty() && (users.len() > 1 || !users.contains(module))
            })
        })
    }

    /// Public functions without production use outside their module that tests
    /// reach, with the test locations.
    pub fn test_only_functions(
        &self,
        tests: &[TestModule<'_>],
    ) -> Vec<(&Member<'a>, Vec<(String, usize)>)> {
        let mut references: HashMap<Target, BTreeSet<(String, usize)>> = HashMap::new();
        for module in tests {
            let imports = module
                .imports
                .iter()
                .map(|(statement, name)| (module.line(statement.start()), name));
            let attributes = module
                .attributes
                .iter()
                .map(|(node, name)| (module.line(node.start()), name));
            for (line, qualified) in imports.chain(attributes) {
                if let Some(target) = resolve_dotted(qualified, &self.namespaces) {
                    references
                        .entry(target)
                        .or_default()
                        .insert((module.path.to_owned(), line));
                }
            }
        }
        self.members
            .iter()
            .filter(|member| {
                member.kind == MemberKind::Function
                    && !member.node.name.starts_with('_')
                    && !member.production_external_use
            })
            .filter_map(|member| {
                let key = (member.source.module.clone(), Some(member.node.name.clone()));
                references
                    .get(&key)
                    .filter(|uses| !uses.is_empty())
                    .map(|uses| (member, uses.iter().cloned().collect()))
            })
            .collect()
    }

    /// Fields without any same-named access outside production, tests included.
    pub fn internal_attributes(
        &self,
        boundary_for: &dyn Fn(&str) -> String,
    ) -> Vec<&AttributeMember<'a>> {
        self.attributes(boundary_for)
            .into_iter()
            .filter(|member| {
                let (total, production) = self.counts(&member.name);
                total == production
            })
            .collect()
    }

    pub fn test_only_attributes(
        &self,
        tests: &[TestModule<'_>],
        boundary_for: &dyn Fn(&str) -> String,
    ) -> Vec<(&AttributeMember<'a>, Vec<(String, usize)>)> {
        let mut references: HashMap<(String, String, String), BTreeSet<(String, usize)>> =
            HashMap::new();
        for module in tests {
            let attributes = module.attributes.iter().map(|(node, name)| (*node, name));
            let instances = module
                .instance_attributes
                .iter()
                .map(|use_| (use_.node, &use_.qualified));
            for (node, qualified) in attributes.chain(instances) {
                if !is_load(node) {
                    continue;
                }
                let (owner_name, name) = rpartition(qualified);
                let (module_name, owner) = rpartition(owner_name);
                if let Some((target, Some(class))) =
                    resolve_export(module_name, owner, &self.namespaces)
                {
                    references
                        .entry((target, class, name.to_owned()))
                        .or_default()
                        .insert((module.path.to_owned(), module.line(node.start())));
                }
            }
        }
        self.attributes(boundary_for)
            .into_iter()
            .filter_map(|member| {
                let key = (
                    member.source.module.clone(),
                    member.owner.name.clone(),
                    member.name.clone(),
                );
                references
                    .get(&key)
                    .filter(|uses| !uses.is_empty())
                    .map(|uses| (member, uses.iter().cloned().collect()))
            })
            .collect()
    }

    fn attributes(&self, boundary_for: &dyn Fn(&str) -> String) -> Vec<&AttributeMember<'a>> {
        let mut found = Vec::new();
        for file in &self.files {
            if file.attribute_candidates.is_empty() {
                continue;
            }
            let module = file.source.module.as_str();
            let boundary = boundary_for(module);
            for candidate in &file.attribute_candidates {
                let key = (module.to_owned(), Some(candidate.owner.name.clone()));
                if self.inherited.contains(&key) || self.public_exports.contains(&key) {
                    continue;
                }
                let uses = file
                    .attributes
                    .get(&candidate.name)
                    .map_or(&[][..], Vec::as_slice);
                let local = if boundary == "module" {
                    uses.len()
                } else {
                    uses.iter()
                        .filter(|use_| use_.owner == Some(candidate.owner.line) && use_.internal)
                        .count()
                };
                if self.counts(&candidate.name).1 == local {
                    found.push(candidate);
                }
            }
        }
        found
    }
}

/// `str.rpartition('.')` without the separator.
fn rpartition(text: &str) -> (&str, &str) {
    text.rsplit_once('.').unwrap_or(("", text))
}

fn is_load(node: &Expr) -> bool {
    match node {
        Expr::Attribute(item) => item.ctx == ExprContext::Load,
        Expr::Name(item) => item.ctx == ExprContext::Load,
        Expr::Subscript(item) => item.ctx == ExprContext::Load,
        _ => false,
    }
}

fn resolve_references(file: &FileFacts<'_>, namespaces: &HashMap<&str, Namespace>) -> Resolved {
    let mut cache: HashMap<String, Option<Target>> = HashMap::new();
    let mut resolve = |name: &str| -> Option<Target> {
        cache
            .entry(name.to_owned())
            .or_insert_with(|| resolve_dotted(name, namespaces))
            .clone()
    };
    let mut targets: Vec<Target> = file.stars.iter().map(|star| (star.clone(), None)).collect();
    for name in &file.imports {
        if let Some(target @ (_, Some(_))) = resolve(name) {
            targets.push(target);
        }
    }
    for name in &file.loads {
        if let Some(target) = resolve(name) {
            targets.push(target);
        }
    }
    let mut named = |names: &[String]| -> Vec<Target> {
        names
            .iter()
            .filter_map(|name| resolve(name).filter(|target| target.1.is_some()))
            .collect()
    };
    let inherited = named(&file.bases);
    let public_exports = named(&file.public_exports);
    Resolved {
        targets,
        inherited,
        public_exports,
    }
}

/// The module's view needed to qualify names the way `_qualified` does.
struct Scope<'a> {
    module: &'a str,
    bindings: Names<'a>,
    classes: HashSet<&'a str>,
}

impl Scope<'_> {
    fn qualified(&self, expression: &Expr) -> Option<String> {
        match expression {
            Expr::Name(node) => {
                let name = node.id.as_str();
                Some(self.bindings.get(name).cloned().unwrap_or_else(|| {
                    if self.classes.contains(name) {
                        format!("{}.{name}", self.module)
                    } else {
                        name.to_owned()
                    }
                }))
            }
            Expr::Attribute(node) => self
                .qualified(&node.value)
                .filter(|parent| !parent.is_empty())
                .map(|parent| format!("{parent}.{}", node.attr)),
            Expr::Subscript(node) => self.qualified(&node.value),
            _ => None,
        }
    }
}

fn file_facts<'a>(
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    production: bool,
    namespaces: &HashMap<&str, Namespace>,
) -> FileFacts<'a> {
    let tree = parsed.module();
    let scope = Scope {
        module: &source.module,
        bindings: import_bindings(tree, &source.module, source.is_package),
        classes: tree
            .body
            .iter()
            .filter_map(|statement| match statement {
                Stmt::ClassDef(node) => Some(node.name.as_str()),
                _ => None,
            })
            .collect(),
    };
    let mut visitor = UseVisitor {
        parsed,
        scope: &scope,
        owner: None,
        roots: HashSet::new(),
        depth: 0,
        instance: None,
        order: Vec::new(),
        uses: HashMap::new(),
    };
    visitor.visit_body(&tree.body);
    let (imports, loads, stars) = reference_names(tree, source);
    let mut bases = Vec::new();
    for statement in walk_statements(tree) {
        if let Stmt::ClassDef(node) = statement {
            bases.extend(node.bases().iter().filter_map(|base| scope.qualified(base)));
        }
    }
    let mut facts = FileFacts {
        source,
        production,
        candidates: Vec::new(),
        imports,
        loads,
        stars,
        attributes: visitor
            .uses
            .iter()
            .map(|(name, uses)| {
                let lines = uses
                    .iter()
                    .map(|use_| AttributeLine {
                        line: use_.line,
                        owner: use_.owner.map(|owner| class_definition(parsed, owner).line),
                        internal: use_.internal,
                    })
                    .collect();
                ((*name).to_owned(), lines)
            })
            .collect(),
        bases,
        attribute_candidates: Vec::new(),
        public_exports: Vec::new(),
    };
    if !production {
        return facts;
    }
    let definitions = definition_counts(&tree.body);
    let occupied: Arc<HashSet<String>> = Arc::new(
        namespaces
            .get(source.module.as_str())
            .map(|namespace| {
                namespace
                    .defined
                    .iter()
                    .chain(namespace.imported.keys())
                    .cloned()
                    .collect()
            })
            .unwrap_or_default(),
    );
    let exports = explicit_exports(tree);
    let mut public_names: Vec<&str> = match &exports {
        None => occupied.iter().map(String::as_str).collect(),
        Some(exports) if occupied.contains("__all__") => exports.iter().copied().collect(),
        Some(_) if source.is_package => occupied
            .iter()
            .map(String::as_str)
            .filter(|name| !name.starts_with('_'))
            .collect(),
        Some(_) => Vec::new(),
    };
    public_names.sort_unstable();
    facts.public_exports = public_names
        .into_iter()
        .map(|name| format!("{}.{name}", source.module))
        .collect();
    let mut name_loads: HashMap<&str, BTreeSet<usize>> = HashMap::new();
    let mut collector = NameLoads { found: Vec::new() };
    collector.visit_body(&tree.body);
    for (name, offset) in collector.found {
        name_loads
            .entry(name)
            .or_default()
            .insert(parsed.text.line_at(offset.into()));
    }
    for statement in &tree.body {
        match statement {
            Stmt::FunctionDef(node) => {
                let name = node.name.as_str();
                if definitions.get(name) == Some(&1)
                    && node.decorator_list.is_empty()
                    && exports
                        .as_ref()
                        .is_some_and(|exports| !exports.contains(name))
                {
                    facts.candidates.push(Member {
                        source,
                        owner: None,
                        node: function_definition(parsed, node),
                        kind: MemberKind::Function,
                        internal_references: name_loads
                            .get(name)
                            .into_iter()
                            .flatten()
                            .copied()
                            .collect(),
                        external_use: false,
                        production_external_use: false,
                        class_dependencies: false,
                        occupied_names: Arc::clone(&occupied),
                        module_names: Arc::clone(&occupied),
                    });
                }
            }
            Stmt::ClassDef(node)
                if definitions.get(node.name.as_str()) == Some(&1)
                    && node.decorator_list.is_empty()
                    && node.keywords().is_empty()
                    && node.bases().iter().all(|base| {
                        scope
                            .qualified(base)
                            .is_some_and(|name| OBJECT.contains(&name.as_str()))
                    }) =>
            {
                let shadows = definitions.contains_key("staticmethod");
                facts.candidates.extend(methods_in(
                    node, source, parsed, &scope, &visitor, shadows, &occupied,
                ));
                facts
                    .attribute_candidates
                    .extend(attribute_candidates(node, source, parsed, &visitor));
            }
            _ => {}
        }
    }
    facts
}

fn function_definition(parsed: &ParsedSource, node: &StmtFunctionDef) -> Definition {
    let (line, column) = parsed.text.position(parsed.function_start(node).into());
    Definition {
        name: node.name.to_string(),
        line,
        column: column + 1,
    }
}

fn class_definition(parsed: &ParsedSource, node: &StmtClassDef) -> Definition {
    let (line, column) = parsed.text.position(parsed.class_start(node).into());
    Definition {
        name: node.name.to_string(),
        line,
        column: column + 1,
    }
}

fn place(parsed: &ParsedSource, name: &str, offset: TextSize) -> Definition {
    let (line, column) = parsed.text.position(offset.into());
    Definition {
        name: name.to_owned(),
        line,
        column: column + 1,
    }
}

/// The names imported, loaded and star-imported, like `reference_names`.
fn reference_names(
    tree: &ModModule,
    source: &SourceFile,
) -> (BTreeSet<String>, BTreeSet<String>, BTreeSet<String>) {
    let mut imports = BTreeSet::new();
    let mut stars = BTreeSet::new();
    // Every possible import binding: a same-named import in another local scope
    // must not hide a real production consumer elsewhere in the file.
    let mut bindings: HashMap<&str, BTreeSet<String>> = HashMap::new();
    for statement in walk_statements(tree) {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let full = alias.name.as_str();
                    let top = full.split('.').next().unwrap_or(full);
                    let (name, target) = match &alias.asname {
                        Some(local) => (local.as_str(), full),
                        None => (top, top),
                    };
                    bindings.entry(name).or_default().insert(target.to_owned());
                }
            }
            Stmt::ImportFrom(node) => {
                let base = resolve_relative(
                    &source.module,
                    source.is_package,
                    node.level,
                    node.module.as_ref().map(|target| target.as_str()),
                );
                for alias in &node.names {
                    if alias.name.as_str() == "*" {
                        stars.insert(base.clone());
                        continue;
                    }
                    let qualified = format!("{base}.{}", alias.name);
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    bindings.entry(local).or_default().insert(qualified.clone());
                    imports.insert(qualified);
                }
            }
            _ => {}
        }
    }
    let mut loads = ReferenceLoads {
        bindings: &bindings,
        found: BTreeSet::new(),
    };
    loads.visit_body(&tree.body);
    (imports, loads.found, stars)
}

/// Loaded names and attribute chains, except the value of an enclosing attribute.
struct ReferenceLoads<'b> {
    bindings: &'b HashMap<&'b str, BTreeSet<String>>,
    found: BTreeSet<String>,
}

impl ReferenceLoads<'_> {
    fn qualified(&self, expression: &Expr) -> Vec<String> {
        match expression {
            Expr::Name(node) => self
                .bindings
                .get(node.id.as_str())
                .map(|targets| targets.iter().cloned().collect())
                .unwrap_or_default(),
            Expr::Attribute(node) => self
                .qualified(&node.value)
                .into_iter()
                .map(|base| format!("{base}.{}", node.attr))
                .collect(),
            _ => Vec::new(),
        }
    }

    /// The value of an attribute: its own name or chain is not a separate load.
    fn visit_inner(&mut self, expression: &Expr) {
        match expression {
            Expr::Name(_) => {}
            Expr::Attribute(node) => self.visit_inner(&node.value),
            other => self.visit_expr(other),
        }
    }
}

impl<'a> Visitor<'a> for ReferenceLoads<'_> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Name(node) => {
                if node.ctx == ExprContext::Load {
                    let found = self.qualified(expression);
                    self.found.extend(found);
                }
            }
            Expr::Attribute(node) => {
                if node.ctx == ExprContext::Load {
                    let found = self.qualified(expression);
                    self.found.extend(found);
                }
                self.visit_inner(&node.value);
            }
            other => visitor::walk_expr(self, other),
        }
    }
}

struct NameLoads<'a> {
    found: Vec<(&'a str, TextSize)>,
}

impl<'a> Visitor<'a> for NameLoads<'a> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(node) = expression
            && node.ctx == ExprContext::Load
        {
            self.found.push((node.id.as_str(), node.start()));
        }
        visitor::walk_expr(self, expression);
    }
}

/// How often each name is bound, like `definition_bindings` over `statements`.
pub fn definition_counts(statements: &[Stmt]) -> HashMap<&str, usize> {
    let mut counter = DefinitionCounter {
        counts: HashMap::new(),
    };
    counter.visit_body(statements);
    counter.counts
}

struct DefinitionCounter<'a> {
    counts: HashMap<&'a str, usize>,
}

impl<'a> Visitor<'a> for DefinitionCounter<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => *self.counts.entry(node.name.as_str()).or_default() += 1,
            Stmt::ClassDef(node) => *self.counts.entry(node.name.as_str()).or_default() += 1,
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(node) = expression
            && matches!(node.ctx, ExprContext::Store | ExprContext::Del)
        {
            *self.counts.entry(node.id.as_str()).or_default() += 1;
        }
        visitor::walk_expr(self, expression);
    }
}

fn methods_in<'a>(
    owner: &'a StmtClassDef,
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    scope: &Scope<'_>,
    visitor: &UseVisitor<'a, '_>,
    shadows: bool,
    module_names: &Arc<HashSet<String>>,
) -> Vec<Member<'a>> {
    let definitions = definition_counts(&owner.body);
    let occupied: Arc<HashSet<String>> = Arc::new(
        definitions
            .keys()
            .map(|name| (*name).to_owned())
            .chain(visitor.internal_names(owner))
            .collect(),
    );
    let owner_definition = class_definition(parsed, owner);
    let mut found = Vec::new();
    for statement in &owner.body {
        let Stmt::FunctionDef(node) = statement else {
            continue;
        };
        if definitions.get(node.name.as_str()) != Some(&1) {
            continue;
        }
        let decorators: Vec<Option<String>> = node
            .decorator_list
            .iter()
            .map(|decorator| scope.qualified(&decorator.expression))
            .collect();
        let positional =
            !node.parameters.posonlyargs.is_empty() || !node.parameters.args.is_empty();
        let kind = if decorators.is_empty() && positional {
            MemberKind::Method
        } else if let [Some(decorator)] = decorators.as_slice()
            && STATICMETHOD.contains(&decorator.as_str())
            && !shadows
        {
            MemberKind::StaticMethod
        } else {
            continue;
        };
        found.push(Member {
            source,
            owner: Some(owner_definition.clone()),
            node: function_definition(parsed, node),
            kind,
            internal_references: Vec::new(),
            external_use: false,
            production_external_use: false,
            class_dependencies: kind == MemberKind::StaticMethod && class_dependencies(node, owner),
            occupied_names: Arc::clone(&occupied),
            module_names: Arc::clone(module_names),
        });
    }
    found
}

fn class_dependencies(node: &StmtFunctionDef, owner: &StmtClassDef) -> bool {
    let mut names: HashSet<&str> = HashSet::from(["__class__", "super", owner.name.as_str()]);
    if let Some(parameters) = &owner.type_params {
        names.extend(
            parameters
                .type_params
                .iter()
                .map(|parameter| parameter.name().as_str()),
        );
    }
    for statement in &owner.body {
        match statement {
            Stmt::ClassDef(item) => {
                names.insert(item.name.as_str());
            }
            Stmt::FunctionDef(item) => {
                names.insert(item.name.as_str());
            }
            _ => {}
        }
    }
    let mut stores = NameStores { found: Vec::new() };
    for statement in &owner.body {
        if !matches!(statement, Stmt::FunctionDef(_)) {
            stores.visit_stmt(statement);
        }
    }
    names.extend(stores.found);
    let mut loads = NameLoads { found: Vec::new() };
    loads.visit_stmt_function(node);
    loads.found.iter().any(|(name, _)| names.contains(name))
}

impl<'a> NameLoads<'a> {
    fn visit_stmt_function(&mut self, node: &'a StmtFunctionDef) {
        for decorator in &node.decorator_list {
            self.visit_decorator(decorator);
        }
        if let Some(parameters) = &node.type_params {
            self.visit_type_params(parameters);
        }
        self.visit_parameters(&node.parameters);
        if let Some(returns) = &node.returns {
            self.visit_annotation(returns);
        }
        self.visit_body(&node.body);
    }
}

struct NameStores<'a> {
    found: Vec<&'a str>,
}

impl<'a> Visitor<'a> for NameStores<'a> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(node) = expression
            && node.ctx == ExprContext::Store
        {
            self.found.push(node.id.as_str());
        }
        visitor::walk_expr(self, expression);
    }
}

fn attribute_candidates<'a>(
    owner: &'a StmtClassDef,
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    visitor: &UseVisitor<'a, '_>,
) -> Vec<AttributeMember<'a>> {
    let definitions = definition_counts(&owner.body);
    if ATTRIBUTE_HOOKS
        .iter()
        .any(|hook| definitions.contains_key(hook))
    {
        return Vec::new();
    }
    let mut order: Vec<&str> = Vec::new();
    let mut candidates: HashMap<&str, (TextSize, &'static str)> = HashMap::new();
    for &name in &visitor.order {
        for use_ in &visitor.uses[name] {
            if use_.owner.is_some_and(|item| std::ptr::eq(item, owner))
                && !definitions.contains_key(name)
                && let Some(definition) = use_.definition
                && !candidates.contains_key(name)
            {
                candidates.insert(name, (definition, "instance-attribute"));
                order.push(name);
            }
        }
    }
    for statement in &owner.body {
        let (targets, value): (&[Expr], Option<&Expr>) = match statement {
            Stmt::Assign(node) => (&node.targets, Some(&node.value)),
            Stmt::AnnAssign(node) => (
                std::slice::from_ref(node.target.as_ref()),
                node.value.as_deref(),
            ),
            _ => continue,
        };
        // A call may construct a descriptor rather than ordinary class data.
        if value.is_some_and(|value| {
            !(super::nodes::is_constant(value)
                || matches!(
                    value,
                    Expr::List(_) | Expr::Tuple(_) | Expr::Set(_) | Expr::Dict(_)
                ))
        }) {
            continue;
        }
        for target in targets {
            if let Expr::Name(node) = target {
                let name = node.id.as_str();
                if definitions.get(name) == Some(&1) && !candidates.contains_key(name) {
                    candidates.insert(name, (node.start(), "class-attribute"));
                    order.push(name);
                }
            }
        }
    }
    let occupied: HashSet<String> = definitions
        .keys()
        .map(|name| (*name).to_owned())
        .chain(visitor.internal_names(owner))
        .collect();
    let owner_definition = class_definition(parsed, owner);
    order
        .into_iter()
        .filter(|name| !name.starts_with('_'))
        .map(|name| {
            let (offset, kind) = candidates[name];
            let internal_references: BTreeSet<usize> = visitor
                .uses
                .get(name)
                .into_iter()
                .flatten()
                .filter(|use_| {
                    use_.internal && use_.owner.is_some_and(|item| std::ptr::eq(item, owner))
                })
                .map(|use_| use_.line)
                .collect();
            AttributeMember {
                source,
                owner: owner_definition.clone(),
                node: place(parsed, name, offset),
                name: name.to_owned(),
                kind,
                internal_references: internal_references.into_iter().collect(),
                occupied_names: occupied.clone(),
            }
        })
        .collect()
}

struct AttributeUse<'a> {
    line: usize,
    owner: Option<&'a StmtClassDef>,
    internal: bool,
    /// Where an assignment through the instance parameter defines the attribute.
    definition: Option<TextSize>,
}

/// Attribute accesses by name, and whether they go through a class's own receiver.
struct UseVisitor<'a, 's> {
    parsed: &'a ParsedSource,
    scope: &'s Scope<'s>,
    owner: Option<&'a StmtClassDef>,
    roots: HashSet<&'a str>,
    depth: usize,
    instance: Option<&'a str>,
    order: Vec<&'a str>,
    uses: HashMap<&'a str, Vec<AttributeUse<'a>>>,
}

impl<'a> UseVisitor<'a, '_> {
    fn record(
        &mut self,
        name: &'a str,
        expression: &Expr,
        offset: TextSize,
        definition: Option<TextSize>,
    ) {
        let internal =
            matches!(expression, Expr::Name(node) if self.roots.contains(node.id.as_str()));
        let use_ = AttributeUse {
            line: self.parsed.text.line_at(offset.into()),
            owner: self.owner,
            internal,
            definition,
        };
        let entry = self.uses.entry(name).or_insert_with(|| {
            self.order.push(name);
            Vec::new()
        });
        entry.push(use_);
    }

    /// Names the class reaches through its own receivers.
    fn internal_names(&self, owner: &StmtClassDef) -> impl Iterator<Item = String> + '_ {
        let owner = std::ptr::from_ref(owner);
        self.uses
            .iter()
            .filter(move |(_, uses)| {
                uses.iter().any(|use_| {
                    use_.internal && use_.owner.is_some_and(|item| std::ptr::eq(item, owner))
                })
            })
            .map(|(name, _)| (*name).to_owned())
    }

    fn visit_function(&mut self, statement: &'a Stmt, node: &'a StmtFunctionDef) {
        // Only a class establishes receiver roots. Nested classes still visit their
        // own methods with the usual shadowing and rebinding checks.
        let Some(owner) = self.owner else {
            walk_stmt_in_field_order(self, statement);
            return;
        };
        let previous = self.roots.clone();
        let previous_instance = self.instance;
        let parameters = &node.parameters;
        let first = parameters
            .posonlyargs
            .iter()
            .chain(&parameters.args)
            .next()
            .map(|item| item.parameter.name.as_str());
        let bound: HashSet<&str> = definition_counts(&node.body).into_keys().collect();
        let mut roots = if self.depth == 0 {
            HashSet::from([owner.name.as_str()])
        } else {
            previous.clone()
        };
        for parameter in parameters.iter() {
            roots.remove(parameter.name().as_str());
        }
        roots.retain(|name| !bound.contains(name));
        let decorators: Vec<Option<String>> = node
            .decorator_list
            .iter()
            .map(|decorator| self.scope.qualified(&decorator.expression))
            .collect();
        let is_static = decorators.iter().any(|item| {
            item.as_deref()
                .is_some_and(|name| STATICMETHOD.contains(&name))
        });
        if self.depth == 0
            && let Some(first) = first
            && !is_static
            && !bound.contains(first)
        {
            roots.insert(first);
        }
        self.roots = roots;
        self.instance = first.filter(|_| self.depth == 0 && decorators.is_empty());
        self.depth += 1;
        walk_stmt_in_field_order(self, statement);
        self.depth -= 1;
        self.roots = previous;
        self.instance = previous_instance;
    }

    fn without(&mut self, names: impl IntoIterator<Item = &'a str>, expression: &'a Expr) {
        let previous = self.roots.clone();
        for name in names {
            self.roots.remove(name);
        }
        walk_expr_in_field_order(self, expression);
        self.roots = previous;
    }
}

impl<'a> Visitor<'a> for UseVisitor<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::ClassDef(node) => {
                let saved = (
                    self.owner,
                    std::mem::take(&mut self.roots),
                    self.depth,
                    self.instance,
                );
                self.owner = Some(node);
                self.depth = 0;
                self.instance = None;
                walk_stmt_in_field_order(self, statement);
                (self.owner, self.roots, self.depth, self.instance) = saved;
            }
            Stmt::FunctionDef(node) => self.visit_function(statement, node),
            _ => walk_stmt_in_field_order(self, statement),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Lambda(node) => {
                let names: Vec<&str> = node
                    .parameters
                    .iter()
                    .flat_map(|parameters| parameters.iter())
                    .map(|parameter| parameter.name().as_str())
                    .collect();
                self.without(names, expression);
            }
            Expr::ListComp(_) | Expr::SetComp(_) | Expr::DictComp(_) | Expr::Generator(_) => {
                let generators = match expression {
                    Expr::ListComp(node) => &node.generators[..],
                    Expr::SetComp(node) => &node.generators[..],
                    Expr::DictComp(node) => &node.generators[..],
                    Expr::Generator(node) => &node.generators[..],
                    _ => unreachable!(),
                };
                let names: Vec<&str> = generators
                    .iter()
                    .flat_map(|generator| names_in(&generator.target))
                    .collect();
                self.without(names, expression);
            }
            Expr::Attribute(node) => {
                let definition = (node.ctx == ExprContext::Store
                    && matches!(node.value.as_ref(), Expr::Name(name)
                        if Some(name.id.as_str()) == self.instance
                            && self.roots.contains(name.id.as_str())))
                .then(|| node.start());
                self.record(node.attr.as_str(), &node.value, node.start(), definition);
                walk_expr_in_field_order(self, expression);
            }
            Expr::Call(node) => {
                if let Expr::Name(function) = node.func.as_ref()
                    && REFLECTION.contains(&function.id.as_str())
                    && node.arguments.args.len() >= REFLECTION_ARGUMENTS
                    && let Expr::StringLiteral(name) = &node.arguments.args[1]
                {
                    self.record(
                        name.value.to_str(),
                        &node.arguments.args[0],
                        node.start(),
                        None,
                    );
                }
                walk_expr_in_field_order(self, expression);
            }
            _ => walk_expr_in_field_order(self, expression),
        }
    }
}
