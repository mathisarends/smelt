//! CPX009: a group of parameter names that several functions share.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::bindings::{Names, import_bindings, qualified_name};
use crate::checker::analysis::namespace::{Namespace, project_namespaces, resolve_export};
use crate::checker::analysis::unparse::unparse;
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt, StmtFunctionDef};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

// Fewer shared names are often a coincidence of common names such as `user_id`.
const MIN_SIZE: usize = 5;
const MIN_FUNCTIONS: usize = 3;
// One family repeating its own signature is a design, not a clump.
const MIN_FAMILIES: usize = 2;
const RECEIVERS: [&str; 2] = ["self", "cls"];
const STATICMETHODS: [&str; 2] = ["staticmethod", "builtins.staticmethod"];
const OVERLOADS: [&str; 2] = ["typing.overload", "typing_extensions.overload"];

/// A function with enough named parameters to share a group.
struct Signature<'a> {
    source: &'a SourceFile,
    name: String,
    line: usize,
    column: usize,
    parameters: BTreeSet<&'a str>,
    annotations: HashMap<&'a str, String>,
    family: String,
    /// The families of callables it passes a whole group on to.
    forwards: Vec<String>,
}

/// The top-level functions and classes of each project module.
struct Definitions<'a> {
    functions: HashMap<&'a str, HashSet<&'a str>>,
    classes: HashMap<&'a str, HashSet<&'a str>>,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let namespaces = project_namespaces(context.sources, &context.files.packages);
    let mut sources: Vec<&(&SourceFile, &ParsedSource)> = context.sources.iter().collect();
    sources.sort_by(|left, right| left.0.path.cmp(&right.0.path));
    let definitions = Definitions {
        functions: sources
            .iter()
            .map(|(source, parsed)| (source.module.as_str(), top_level(parsed, true)))
            .collect(),
        classes: sources
            .iter()
            .map(|(source, parsed)| (source.module.as_str(), top_level(parsed, false)))
            .collect(),
    };
    let signatures: Vec<Signature<'_>> = sources
        .par_iter()
        .flat_map_iter(|&&(source, parsed)| signatures(source, parsed, &namespaces, &definitions))
        .collect();
    let families = Families::of(&signatures);
    let mut groups: BTreeMap<Vec<usize>, BTreeSet<&str>> = BTreeMap::new();
    for (index, left) in signatures.iter().enumerate() {
        for right in &signatures[index + 1..] {
            let shared: BTreeSet<&str> = left
                .parameters
                .intersection(&right.parameters)
                .copied()
                .collect();
            if shared.len() < MIN_SIZE {
                continue;
            }
            let members: Vec<usize> = signatures
                .iter()
                .enumerate()
                .filter(|(_, signature)| signature.parameters.is_superset(&shared))
                .map(|(member, _)| member)
                .collect();
            if members.len() < MIN_FUNCTIONS || families.count(&signatures, &members) < MIN_FAMILIES
            {
                continue;
            }
            // The members' common names are the widest group they share.
            let common = members[1..].iter().fold(
                signatures[members[0]].parameters.clone(),
                |common, &member| {
                    common
                        .intersection(&signatures[member].parameters)
                        .copied()
                        .collect()
                },
            );
            groups.insert(members, common);
        }
    }
    groups
        .iter()
        .map(|(members, group)| {
            report(
                &signatures,
                members,
                group,
                families.count(&signatures, members),
            )
        })
        .collect()
}

fn top_level(parsed: &ParsedSource, functions: bool) -> HashSet<&str> {
    parsed
        .module()
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::FunctionDef(node) if functions => Some(node.name.as_str()),
            Stmt::ClassDef(node) if !functions => Some(node.name.as_str()),
            _ => None,
        })
        .collect()
}

fn signatures<'a>(
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    namespaces: &HashMap<&str, Namespace>,
    definitions: &Definitions<'_>,
) -> Vec<Signature<'a>> {
    let module = parsed.module();
    let mut collector = Collector {
        source,
        parsed,
        resolver: Resolver {
            module: &source.module,
            bindings: import_bindings(module, &source.module, source.is_package),
            namespaces,
            definitions,
        },
        scope: Vec::new(),
        in_class: false,
        found: Vec::new(),
    };
    collector.visit_body(&module.body);
    collector.found
}

/// Every function definition with its dotted scope.
struct Collector<'a, 'r> {
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    resolver: Resolver<'a, 'r>,
    scope: Vec<&'a str>,
    in_class: bool,
    found: Vec<Signature<'a>>,
}

impl<'a> Collector<'a, '_> {
    fn signature(&self, function: &'a StmtFunctionDef) -> Option<Signature<'a>> {
        let decorators: Vec<String> = function
            .decorator_list
            .iter()
            .filter_map(|decorator| self.resolver.qualified(&decorator.expression))
            .collect();
        if decorators
            .iter()
            .any(|name| OVERLOADS.contains(&name.as_str()))
        {
            return None;
        }
        let arguments = &function.parameters;
        let mut named: Vec<_> = arguments
            .posonlyargs
            .iter()
            .chain(&arguments.args)
            .chain(&arguments.kwonlyargs)
            .collect();
        let method = self.in_class;
        if method
            && !decorators
                .iter()
                .any(|name| STATICMETHODS.contains(&name.as_str()))
            && !named.is_empty()
        {
            named.remove(0);
        }
        let parameters: BTreeSet<&str> = named
            .iter()
            .map(|argument| argument.parameter.name.as_str())
            .filter(|name| !RECEIVERS.contains(name))
            .collect();
        if parameters.len() < MIN_SIZE {
            return None;
        }
        let text = &self.parsed.text.text;
        let annotations = named
            .iter()
            .filter_map(|argument| {
                let annotation = argument.parameter.annotation.as_deref()?;
                Some((argument.parameter.name.as_str(), unparse(text, annotation)))
            })
            .collect();
        let name = function.name.as_str();
        let family = if method {
            format!("method:{name}")
        } else {
            let mut dotted = vec![self.source.module.as_str()];
            dotted.extend(&self.scope);
            dotted.push(name);
            format!("function:{}", dotted.join("."))
        };
        let mut forwards = Forwards {
            resolver: &self.resolver,
            parameters: &parameters,
            found: Vec::new(),
        };
        forwards.visit_body(&function.body);
        let forwards = forwards.found;
        let (line, column) = self
            .parsed
            .text
            .position(usize::from(self.parsed.function_start(function)));
        let mut qualified = self.scope.clone();
        qualified.push(name);
        Some(Signature {
            source: self.source,
            name: qualified.join("."),
            line,
            column: column + 1,
            parameters,
            annotations,
            family,
            forwards,
        })
    }
}

impl<'a> Visitor<'a> for Collector<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let (name, class) = match statement {
            Stmt::FunctionDef(function) => {
                if let Some(signature) = self.signature(function) {
                    self.found.push(signature);
                }
                (function.name.as_str(), false)
            }
            Stmt::ClassDef(class) => (class.name.as_str(), true),
            _ => {
                visitor::walk_stmt(self, statement);
                return;
            }
        };
        let outer = self.in_class;
        self.scope.push(name);
        self.in_class = class;
        visitor::walk_stmt(self, statement);
        self.in_class = outer;
        self.scope.pop();
    }
}

/// A project definition a callee names.
#[derive(PartialEq)]
enum Definition {
    Class,
    /// Its qualified name.
    Function(String),
}

/// Resolves a callee to the family of a project function, method or class.
struct Resolver<'a, 'r> {
    module: &'a str,
    bindings: Names<'a>,
    namespaces: &'r HashMap<&'r str, Namespace>,
    definitions: &'r Definitions<'r>,
}

impl Resolver<'_, '_> {
    fn qualified(&self, expression: &Expr) -> Option<String> {
        if let Expr::Name(name) = expression
            && !self.bindings.contains_key(name.id.as_str())
        {
            let local = name.id.as_str();
            let defined = |definitions: &HashMap<&str, HashSet<&str>>| {
                definitions
                    .get(self.module)
                    .is_some_and(|names| names.contains(local))
            };
            if defined(&self.definitions.functions) || defined(&self.definitions.classes) {
                return Some(format!("{}.{local}", self.module));
            }
        }
        qualified_name(expression, &self.bindings)
    }

    fn family(&self, callee: &Expr) -> Option<String> {
        if let Expr::Attribute(attribute) = callee {
            let receiver = match attribute.value.as_ref() {
                Expr::Name(name) => RECEIVERS.contains(&name.id.as_str()),
                Expr::Call(call) => {
                    matches!(call.func.as_ref(), Expr::Name(name) if name.id.as_str() == "super")
                }
                _ => false,
            };
            // A method called through a project class, such as a factory `Entity.create(...)`.
            if receiver || self.project(&attribute.value) == Some(Definition::Class) {
                return Some(format!("method:{}", attribute.attr));
            }
        }
        // A classmethod factory constructs its own class.
        if matches!(callee, Expr::Name(name) if name.id.as_str() == "cls") {
            return Some("method:__init__".to_owned());
        }
        match self.project(callee)? {
            Definition::Class => Some("method:__init__".to_owned()),
            Definition::Function(qualified) => Some(format!("function:{qualified}")),
        }
    }

    /// The project class or top-level function an expression names.
    fn project(&self, expression: &Expr) -> Option<Definition> {
        let qualified = self.qualified(expression)?;
        let (prefix, last) = qualified.rsplit_once('.')?;
        let (module, symbol) = resolve_export(prefix, last, self.namespaces)?;
        let symbol = symbol?;
        let has = |definitions: &HashMap<&str, HashSet<&str>>| {
            definitions
                .get(module.as_str())
                .is_some_and(|names| names.contains(symbol.as_str()))
        };
        if has(&self.definitions.classes) {
            return Some(Definition::Class);
        }
        has(&self.definitions.functions).then(|| Definition::Function(format!("{module}.{symbol}")))
    }
}

/// Calls in a function body that pass a whole group of its parameters on.
struct Forwards<'a, 'r, 'p> {
    resolver: &'r Resolver<'a, 'r>,
    parameters: &'p BTreeSet<&'a str>,
    found: Vec<String>,
}

impl<'b> Visitor<'b> for Forwards<'_, '_, '_> {
    fn visit_expr(&mut self, expression: &'b Expr) {
        if let Expr::Call(call) = expression {
            let passed: HashSet<&str> = call
                .arguments
                .args
                .iter()
                .chain(call.arguments.keywords.iter().map(|keyword| &keyword.value))
                .filter_map(|argument| match argument {
                    Expr::Name(name) if self.parameters.contains(name.id.as_str()) => {
                        Some(name.id.as_str())
                    }
                    _ => None,
                })
                .collect();
            if passed.len() >= MIN_SIZE
                && let Some(family) = self.resolver.family(&call.func)
            {
                self.found.push(family);
            }
        }
        visitor::walk_expr(self, expression);
    }
}

/// Override families joined by forwarding calls.
struct Families {
    index: HashMap<String, usize>,
    parents: Vec<usize>,
}

impl Families {
    fn of(signatures: &[Signature<'_>]) -> Self {
        let mut families = Self {
            index: HashMap::new(),
            parents: Vec::new(),
        };
        for signature in signatures {
            let own = families.id(&signature.family);
            for target in &signature.forwards {
                let target = families.id(target);
                families.join(own, target);
            }
        }
        families
    }

    fn id(&mut self, family: &str) -> usize {
        if let Some(&id) = self.index.get(family) {
            return id;
        }
        let id = self.parents.len();
        self.parents.push(id);
        self.index.insert(family.to_owned(), id);
        id
    }

    fn root(&self, mut id: usize) -> usize {
        while self.parents[id] != id {
            id = self.parents[id];
        }
        id
    }

    fn join(&mut self, left: usize, right: usize) {
        let (left, right) = (self.root(left), self.root(right));
        self.parents[left.max(right)] = left.min(right);
    }

    fn count(&self, signatures: &[Signature<'_>], members: &[usize]) -> usize {
        members
            .iter()
            .map(|&member| self.root(self.index[&signatures[member].family]))
            .collect::<HashSet<_>>()
            .len()
    }
}

fn report(
    signatures: &[Signature<'_>],
    members: &[usize],
    group: &BTreeSet<&str>,
    families: usize,
) -> StructureFinding {
    let first = &signatures[members[0]];
    let names: Vec<&str> = group.iter().copied().collect();
    let modules: BTreeSet<&str> = members
        .iter()
        .map(|&member| signatures[member].source.module.as_str())
        .collect();
    let locations: Vec<Value> = members
        .iter()
        .map(|&member| {
            let signature = &signatures[member];
            json!({
                "path": signature.source.path,
                "line": signature.line,
                "function": signature.name,
            })
        })
        .collect();
    // A parameter's annotation when every member writes the same one.
    let annotations: BTreeMap<&str, Option<&str>> = names
        .iter()
        .map(|&name| {
            let mut written = members
                .iter()
                .map(|&member| signatures[member].annotations.get(name).map(String::as_str));
            let first = written.next().flatten();
            let shared = first.filter(|first| written.all(|other| other == Some(*first)));
            (name, shared)
        })
        .collect();
    let listed = names.join(", ");
    let module_count = modules.len();
    let in_modules = if module_count == 1 {
        "1 module".to_owned()
    } else {
        format!("{module_count} modules")
    };
    StructureFinding {
        message: format!(
            "`{listed}` travel together through {} functions in {in_modules}; consider a value object such as a dataclass",
            members.len()
        ),
        path: first.source.path.clone(),
        position: Some((first.line, first.column)),
        source_module: Some(first.source.module.clone()),
        expected: json!({
            "parameters": names,
            "annotations": annotations,
            "functions": members.len(),
            "modules": module_count,
            "families": families,
            "locations": locations,
            "automatic_rewrite": false,
        }),
        hint: format!(
            "Name the concept behind {listed} as one value object, such as a frozen dataclass, and pass it instead of the separate parameters; review the locations together."
        ),
        ..StructureFinding::default()
    }
}
