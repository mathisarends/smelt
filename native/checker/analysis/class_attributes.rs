//! Project classes, the attributes each one declares and the project class types
//! an annotation names, for rules that reason about a receiver's declared type.
//!
//! Only unconditional top-level classes, functions and type aliases with a single
//! binding in their module are known. Attributes added by `setattr`, monkeypatching
//! or metaclasses are invisible.
use super::bindings::{BindingCounts, Coverage, qualified_name};
use super::namespace::{Namespace, resolve_export};
use super::nodes::{parse_eval, subscript_parts};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::{Expr, Operator, Stmt, StmtClassDef, StmtFunctionDef};
use std::collections::{HashMap, HashSet};

// Alias and inheritance chains are short; a longer one is more likely a cycle.
const MAX_DEPTH: usize = 8;

const BUILTINS: [&str; 9] = [
    "list",
    "tuple",
    "set",
    "frozenset",
    "getattr",
    "hasattr",
    "property",
    "staticmethod",
    "classmethod",
];

const COLLECTIONS: [&str; 22] = [
    "builtins.list",
    "builtins.tuple",
    "builtins.set",
    "builtins.frozenset",
    "typing.List",
    "typing.Tuple",
    "typing.Set",
    "typing.FrozenSet",
    "typing.Sequence",
    "typing.MutableSequence",
    "typing.Iterable",
    "typing.Iterator",
    "typing.Collection",
    "typing.AbstractSet",
    "typing.MutableSet",
    "collections.abc.Sequence",
    "collections.abc.MutableSequence",
    "collections.abc.Iterable",
    "collections.abc.Iterator",
    "collections.abc.Collection",
    "collections.abc.Set",
    "collections.abc.MutableSet",
];

const TUPLES: [&str; 2] = ["builtins.tuple", "typing.Tuple"];
const UNIONS: [&str; 2] = ["typing.Union", "typing_extensions.Union"];
const ANNOTATED: [&str; 2] = ["typing.Annotated", "typing_extensions.Annotated"];
const TYPE_ALIASES: [&str; 2] = ["typing.TypeAlias", "typing_extensions.TypeAlias"];
const TYPED_DICTS: [&str; 2] = ["typing.TypedDict", "typing_extensions.TypedDict"];
// Annotations that declare no instance attribute without a value.
const PSEUDO_FIELDS: [&str; 2] = ["ClassVar", "InitVar"];
const PROPERTIES: [&str; 2] = ["builtins.property", "functools.cached_property"];
// Attribute hooks and slots decide at runtime which declared names exist.
const DYNAMIC: [&str; 3] = ["__getattr__", "__getattribute__", "__slots__"];

/// Whether a class and its project bases declare an attribute.
pub enum Lookup {
    /// The qualified name of the first class in the chain that declares it.
    Declared(String),
    Missing,
    /// A class in the chain decides attribute access at runtime.
    Dynamic,
}

/// A value's project class types: as an instance, and as a collection's elements.
#[derive(Clone, Default)]
pub struct Value {
    pub classes: Option<Vec<String>>,
    pub elements: Option<Vec<String>>,
}

struct ClassFacts<'a> {
    module: &'a str,
    names: HashSet<&'a str>,
    /// Annotated attributes, `__init__` parameters assigned to the receiver and
    /// property return types.
    annotations: HashMap<&'a str, &'a Expr>,
    bases: Vec<&'a Expr>,
    dynamic: bool,
}

struct ModuleFacts<'a> {
    bindings: HashMap<String, String>,
    classes: HashMap<&'a str, ClassFacts<'a>>,
    aliases: HashMap<&'a str, &'a Expr>,
    /// Undecorated top-level functions with a return annotation.
    functions: HashMap<&'a str, &'a StmtFunctionDef>,
}

enum Definition<'a> {
    Class(String),
    Alias(&'a str, &'a Expr),
    Function(&'a str, &'a StmtFunctionDef),
}

pub struct ClassAttributes<'a> {
    namespaces: &'a HashMap<&'a str, Namespace>,
    modules: HashMap<&'a str, ModuleFacts<'a>>,
}

impl<'a> ClassAttributes<'a> {
    pub fn new(
        namespaces: &'a HashMap<&'a str, Namespace>,
        sources: &[(&'a SourceFile, &'a ParsedSource)],
    ) -> Self {
        let modules = sources
            .par_iter()
            .filter_map(|&(source, parsed)| {
                let namespace = namespaces.get(source.module.as_str())?;
                Some((
                    source.module.as_str(),
                    module_facts(source, parsed, namespace),
                ))
            })
            .collect();
        Self {
            namespaces,
            modules,
        }
    }

    /// The qualified name `expression` refers to in `module`, unless a name in
    /// `blocked` shadows its root.
    pub fn qualified(
        &self,
        module: &str,
        expression: &Expr,
        blocked: &HashSet<&str>,
    ) -> Option<String> {
        let mut root = expression;
        while let Expr::Attribute(attribute) = root {
            root = &attribute.value;
        }
        if let Expr::Name(name) = root
            && blocked.contains(name.id.as_str())
        {
            return None;
        }
        qualified_name(expression, &self.modules.get(module)?.bindings)
    }

    /// The qualified name of the top-level class `name` of `module`, if known.
    pub fn class_named(&self, module: &str, name: &str) -> Option<String> {
        let facts = self.modules.get(module)?;
        let qualified = format!("{module}.{name}");
        (facts.classes.contains_key(name) && facts.bindings.get(name) == Some(&qualified))
            .then_some(qualified)
    }

    /// The project class types of `annotation`, written in `module`.
    pub fn annotation(&self, module: &str, annotation: &Expr, blocked: &HashSet<&str>) -> Value {
        Value {
            classes: self.classes(module, annotation, blocked, 0),
            elements: self.elements(module, annotation, blocked, 0),
        }
    }

    /// The project classes a call to `function` returns: a constructed project
    /// class, or the annotated return type of a project function.
    pub fn call(
        &self,
        module: &str,
        function: &Expr,
        awaited: bool,
        blocked: &HashSet<&str>,
    ) -> Value {
        let Some(name) = self.qualified(module, function, blocked) else {
            return Value::default();
        };
        match self.definition(&name) {
            Some(Definition::Class(class)) if !awaited => Value {
                classes: Some(vec![class]),
                elements: None,
            },
            Some(Definition::Function(owner, function)) if function.is_async == awaited => function
                .returns
                .as_deref()
                .map_or_else(Value::default, |returns| {
                    self.annotation(owner, returns, &HashSet::new())
                }),
            _ => Value::default(),
        }
    }

    /// The types of `attribute` on an instance of every class in `classes`.
    pub fn member(&self, classes: &[String], attribute: &str) -> Value {
        let mut found = Value {
            classes: Some(Vec::new()),
            elements: Some(Vec::new()),
        };
        for class in classes {
            let Some((module, annotation)) = self.attribute_annotation(class, attribute, 0) else {
                return Value::default();
            };
            let value = self.annotation(module, annotation, &HashSet::new());
            found.classes = merge(found.classes, value.classes);
            found.elements = merge(found.elements, value.elements);
        }
        found
    }

    /// Whether `class` or one of its project bases declares `attribute`.
    pub fn lookup(&self, class: &str, attribute: &str) -> Lookup {
        self.lookup_at(class, attribute, 0)
    }

    fn lookup_at(&self, class: &str, attribute: &str, depth: usize) -> Lookup {
        let Some(facts) = self.class(class).filter(|_| depth < MAX_DEPTH) else {
            return Lookup::Dynamic;
        };
        if facts.dynamic {
            return Lookup::Dynamic;
        }
        let mut found = if facts.names.contains(attribute) {
            Lookup::Declared(class.to_owned())
        } else {
            Lookup::Missing
        };
        for base in &facts.bases {
            // Bases outside the project can only add attributes.
            let Some(bases) = self.classes(facts.module, base, &HashSet::new(), 0) else {
                continue;
            };
            for base in bases {
                match self.lookup_at(&base, attribute, depth + 1) {
                    Lookup::Dynamic => return Lookup::Dynamic,
                    Lookup::Declared(owner) if matches!(found, Lookup::Missing) => {
                        found = Lookup::Declared(owner);
                    }
                    _ => {}
                }
            }
        }
        found
    }

    fn attribute_annotation(
        &self,
        class: &str,
        attribute: &str,
        depth: usize,
    ) -> Option<(&'a str, &'a Expr)> {
        let facts = self.class(class).filter(|_| depth < MAX_DEPTH)?;
        if let Some(&annotation) = facts.annotations.get(attribute) {
            return Some((facts.module, annotation));
        }
        facts.bases.iter().find_map(|base| {
            let [base]: [String; 1] = self
                .classes(facts.module, base, &HashSet::new(), 0)?
                .try_into()
                .ok()?;
            self.attribute_annotation(&base, attribute, depth + 1)
        })
    }

    fn class(&self, qualified: &str) -> Option<&ClassFacts<'a>> {
        let (module, name) = qualified.rsplit_once('.')?;
        self.modules.get(module)?.classes.get(name)
    }

    fn definition(&self, name: &str) -> Option<Definition<'a>> {
        let (prefix, last) = name.rsplit_once('.')?;
        if !self.namespaces.contains_key(prefix) {
            return None;
        }
        let (module, symbol) = resolve_export(prefix, last, self.namespaces)?;
        let symbol = symbol?;
        let (&module, facts) = self.modules.get_key_value(module.as_str())?;
        let qualified = format!("{module}.{symbol}");
        if facts.bindings.get(&symbol) != Some(&qualified) {
            return None;
        }
        let symbol = symbol.as_str();
        if facts.classes.contains_key(symbol) {
            return Some(Definition::Class(qualified));
        }
        if let Some(&alias) = facts.aliases.get(symbol) {
            return Some(Definition::Alias(module, alias));
        }
        facts
            .functions
            .get(symbol)
            .map(|&function| Definition::Function(module, function))
    }

    /// The project classes a type expression names; `None` unless every member of
    /// a union is a project class.
    fn classes(
        &self,
        module: &str,
        node: &Expr,
        blocked: &HashSet<&str>,
        depth: usize,
    ) -> Option<Vec<String>> {
        if depth >= MAX_DEPTH {
            return None;
        }
        match node {
            Expr::StringLiteral(literal) => {
                let parsed = parse_eval(literal.value.to_str())?;
                self.classes(module, parsed.expr(), blocked, depth + 1)
            }
            Expr::BinOp(operation) if operation.op == Operator::BitOr => merge(
                self.classes(module, &operation.left, blocked, depth + 1),
                self.classes(module, &operation.right, blocked, depth + 1),
            ),
            Expr::Subscript(subscript) => {
                let name = self.qualified(module, &subscript.value, blocked)?;
                let parts = subscript_parts(&subscript.slice);
                if UNIONS.contains(&name.as_str()) {
                    return parts.iter().try_fold(Vec::new(), |found, part| {
                        merge(Some(found), self.classes(module, part, blocked, depth + 1))
                    });
                }
                if ANNOTATED.contains(&name.as_str()) {
                    return self.classes(module, parts.first()?, blocked, depth + 1);
                }
                // A parameterized project generic is still its class.
                match self.definition(&name)? {
                    Definition::Class(class) => Some(vec![class]),
                    _ => None,
                }
            }
            Expr::Name(_) | Expr::Attribute(_) => {
                match self.definition(&self.qualified(module, node, blocked)?)? {
                    Definition::Class(class) => Some(vec![class]),
                    Definition::Alias(owner, alias) => {
                        self.classes(owner, alias, &HashSet::new(), depth + 1)
                    }
                    Definition::Function(..) => None,
                }
            }
            _ => None,
        }
    }

    /// The project classes of a collection annotation's elements.
    fn elements(
        &self,
        module: &str,
        node: &Expr,
        blocked: &HashSet<&str>,
        depth: usize,
    ) -> Option<Vec<String>> {
        if depth >= MAX_DEPTH {
            return None;
        }
        match node {
            Expr::StringLiteral(literal) => {
                let parsed = parse_eval(literal.value.to_str())?;
                self.elements(module, parsed.expr(), blocked, depth + 1)
            }
            Expr::Subscript(subscript) => {
                let name = self.qualified(module, &subscript.value, blocked)?;
                if !COLLECTIONS.contains(&name.as_str()) {
                    return None;
                }
                let parts = subscript_parts(&subscript.slice);
                let members = match parts {
                    [single, Expr::EllipsisLiteral(_)] if TUPLES.contains(&name.as_str()) => {
                        std::slice::from_ref(single)
                    }
                    _ if TUPLES.contains(&name.as_str()) => parts,
                    _ => parts.get(..1)?,
                };
                members.iter().try_fold(Vec::new(), |found, member| {
                    merge(
                        Some(found),
                        self.classes(module, member, blocked, depth + 1),
                    )
                })
            }
            Expr::Name(_) | Expr::Attribute(_) => {
                match self.definition(&self.qualified(module, node, blocked)?)? {
                    Definition::Alias(owner, alias) => {
                        self.elements(owner, alias, &HashSet::new(), depth + 1)
                    }
                    _ => None,
                }
            }
            _ => None,
        }
    }
}

/// The union of two class lists, or `None` when either is unknown.
fn merge(left: Option<Vec<String>>, right: Option<Vec<String>>) -> Option<Vec<String>> {
    let mut found = left?;
    for class in right? {
        if !found.contains(&class) {
            found.push(class);
        }
    }
    Some(found)
}

fn module_facts<'a>(
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    namespace: &Namespace,
) -> ModuleFacts<'a> {
    let module = source.module.as_str();
    let tree = parsed.module();
    let counts = BindingCounts::new(tree, Coverage::Full);
    let mut bindings = HashMap::new();
    for (name, target) in &namespace.imported {
        if counts.get(name) == 1 {
            bindings.insert(name.clone(), target.clone());
        }
    }
    for name in &namespace.defined {
        if counts.get(name) == 1 {
            bindings.insert(name.clone(), format!("{module}.{name}"));
        }
    }
    for name in BUILTINS {
        if counts.get(name) == 0 {
            bindings.insert(name.to_owned(), format!("builtins.{name}"));
        }
    }
    let mut facts = ModuleFacts {
        classes: HashMap::new(),
        aliases: HashMap::new(),
        functions: HashMap::new(),
        bindings,
    };
    for statement in &tree.body {
        match statement {
            Stmt::ClassDef(class) => {
                let class_facts = class_facts(module, class, &facts.bindings);
                facts.classes.insert(class.name.as_str(), class_facts);
            }
            Stmt::FunctionDef(function)
                if function.decorator_list.is_empty() && function.returns.is_some() =>
            {
                facts.functions.insert(function.name.as_str(), function);
            }
            Stmt::TypeAlias(alias) if alias.type_params.is_none() => {
                if let Expr::Name(name) = alias.name.as_ref() {
                    facts.aliases.insert(name.id.as_str(), &alias.value);
                }
            }
            Stmt::AnnAssign(assignment)
                if qualified_name(&assignment.annotation, &facts.bindings)
                    .is_some_and(|name| TYPE_ALIASES.contains(&name.as_str())) =>
            {
                if let (Expr::Name(name), Some(value)) =
                    (assignment.target.as_ref(), assignment.value.as_deref())
                {
                    facts.aliases.insert(name.id.as_str(), value);
                }
            }
            Stmt::Assign(assignment) => {
                if let ([Expr::Name(name)], value) = (&*assignment.targets, &*assignment.value)
                    && matches!(
                        value,
                        Expr::Name(_) | Expr::Attribute(_) | Expr::BinOp(_) | Expr::Subscript(_)
                    )
                {
                    facts.aliases.insert(name.id.as_str(), value);
                }
            }
            _ => {}
        }
    }
    facts
}

fn class_facts<'a>(
    module: &'a str,
    node: &'a StmtClassDef,
    bindings: &HashMap<String, String>,
) -> ClassFacts<'a> {
    let bases: Vec<&Expr> = node.bases().iter().collect();
    // Plain classes create no attribute for a bare annotation; field-generating
    // decorators and bases such as pydantic models or named tuples do.
    let fields = !node.decorator_list.is_empty()
        || bases
            .iter()
            .any(|base| qualified_name(base, bindings).as_deref() != Some("builtins.object"));
    let mut facts = ClassFacts {
        module,
        names: HashSet::new(),
        annotations: HashMap::new(),
        dynamic: bases.iter().any(|base| {
            qualified_name(base, bindings).is_some_and(|name| TYPED_DICTS.contains(&name.as_str()))
        }),
        bases,
    };
    for statement in &node.body {
        match statement {
            Stmt::AnnAssign(assignment) => {
                let Expr::Name(target) = assignment.target.as_ref() else {
                    continue;
                };
                let name = target.id.as_str();
                let pseudo = match assignment.annotation.as_ref() {
                    Expr::Subscript(subscript) => is_pseudo_field(&subscript.value),
                    other => is_pseudo_field(other),
                };
                if assignment.value.is_some() || (fields && !pseudo) {
                    facts.names.insert(name);
                }
                facts.annotations.insert(name, &assignment.annotation);
            }
            Stmt::Assign(assignment) => {
                for target in &assignment.targets {
                    if let Expr::Name(target) = target {
                        facts.names.insert(target.id.as_str());
                    }
                }
            }
            Stmt::ClassDef(class) => {
                facts.names.insert(class.name.as_str());
            }
            Stmt::FunctionDef(method) => {
                let name = method.name.as_str();
                facts.names.insert(name);
                let property = method.decorator_list.iter().any(|decorator| {
                    qualified_name(&decorator.expression, bindings)
                        .is_some_and(|name| PROPERTIES.contains(&name.as_str()))
                });
                if property && let Some(returns) = &method.returns {
                    facts.annotations.insert(name, returns);
                }
                if name == "__init__" {
                    initializer_fields(method, &mut facts);
                }
            }
            _ => {}
        }
    }
    facts.dynamic |= DYNAMIC.iter().any(|name| facts.names.contains(name));
    facts
}

fn is_pseudo_field(annotation: &Expr) -> bool {
    let name = match annotation {
        Expr::Name(name) => name.id.as_str(),
        Expr::Attribute(attribute) => attribute.attr.as_str(),
        _ => return false,
    };
    PSEUDO_FIELDS.contains(&name)
}

/// Receiver attributes `__init__` assigns unconditionally, at the top of its body.
fn initializer_fields<'a>(method: &'a StmtFunctionDef, facts: &mut ClassFacts<'a>) {
    let parameters = &method.parameters;
    let mut arguments = parameters.posonlyargs.iter().chain(&parameters.args);
    let Some(receiver) = arguments.next() else {
        return;
    };
    let receiver = receiver.parameter.name.as_str();
    let annotated: HashMap<&str, &Expr> = arguments
        .chain(&parameters.kwonlyargs)
        .filter_map(|argument| {
            let annotation = argument.parameter.annotation.as_deref()?;
            Some((argument.parameter.name.as_str(), annotation))
        })
        .collect();
    let written = BindingCounts::of_statements(&method.body);
    for statement in &method.body {
        let (targets, annotation, value): (&[Expr], _, _) = match statement {
            Stmt::Assign(node) => (&node.targets, None, node.value.as_ref()),
            Stmt::AnnAssign(node) => {
                let Some(value) = node.value.as_deref() else {
                    continue;
                };
                (
                    std::slice::from_ref(node.target.as_ref()),
                    Some(node.annotation.as_ref()),
                    value,
                )
            }
            _ => continue,
        };
        for target in targets {
            let Expr::Attribute(target) = target else {
                continue;
            };
            if !matches!(target.value.as_ref(), Expr::Name(name) if name.id.as_str() == receiver) {
                continue;
            }
            let attribute = target.attr.as_str();
            facts.names.insert(attribute);
            let parameter = match value {
                Expr::Name(name) if written.get(name.id.as_str()) == 0 => {
                    annotated.get(name.id.as_str()).copied()
                }
                _ => None,
            };
            if let Some(annotation) = annotation.or(parameter) {
                facts.annotations.entry(attribute).or_insert(annotation);
            }
        }
    }
}
