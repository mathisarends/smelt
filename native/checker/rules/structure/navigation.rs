//! CPX001: a method call reached through several instance attributes.
//!
//! Like `smelt.analysis.navigation`, the namespace evidence comes from annotations,
//! constructor assignments and property return types, without importing code.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::namespace::{Namespace, project_namespaces, resolve_dotted};
use crate::checker::analysis::nodes::{names_in, parse_eval};
use crate::checker::analysis::unparse::unparse;
use crate::checker::analysis::walk::{TreeNode, children, tree_nodes};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    ExceptHandler, Expr, ExprCall, ExprContext, ModModule, Operator, Pattern, Stmt, StmtClassDef,
    StmtFunctionDef,
};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

// Re-export chains are short; a longer one is more likely a cycle than a design.
const MAX_HOPS: usize = 8;

const VALUES: [&str; 18] = [
    "builtins.str",
    "builtins.bytes",
    "builtins.list",
    "builtins.dict",
    "builtins.set",
    "builtins.frozenset",
    "builtins.tuple",
    "collections.abc.Mapping",
    "collections.abc.MutableMapping",
    "collections.abc.Sequence",
    "collections.abc.MutableSequence",
    "collections.abc.Set",
    "collections.abc.MutableSet",
    "pathlib.Path",
    "pathlib.PurePath",
    "pathlib.PosixPath",
    "pathlib.WindowsPath",
    "io.IOBase",
];

const NAMESPACES: [&str; 3] = [
    "types.ModuleType",
    "types.SimpleNamespace",
    "subprocess.Popen",
];

const IMPLICIT: [&str; 3] = ["__new__", "__init_subclass__", "__class_getitem__"];

const HINT: &str = "Inspect the receiver type and public API contract: this may be intentional \
namespace navigation. If it crosses collaborator responsibilities, consider direct injection or \
delegation. The finding is a review candidate, not a confirmed defect.";

/// A statically known type; `fields` are the attributes of a `SimpleNamespace`.
#[derive(Clone, PartialEq)]
struct Type {
    name: String,
    fields: Option<HashMap<String, Option<Type>>>,
}

impl Type {
    fn named(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            fields: None,
        }
    }
}

/// Namespace evidence a project declares for types Smelt cannot analyze.
#[derive(Default)]
struct Declared {
    namespaces: HashSet<String>,
    factories: HashSet<String>,
}

/// How far a stored path is proven to be namespace navigation.
struct Evidence {
    root_type: Option<String>,
    /// The number of attributes up to the first unproven one; `None` when proven.
    unresolved_at: Option<usize>,
    unresolved_type: Option<String>,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let mut evidence = Namespaces {
        namespaces: project_namespaces(context.sources, &context.files.packages),
        trees: context
            .sources
            .iter()
            .map(|&(source, parsed)| (source.module.as_str(), parsed.module()))
            .collect(),
        definitions: HashMap::new(),
        records: HashMap::new(),
        owners: HashMap::new(),
    };
    let mut sources: Vec<&(&SourceFile, &ParsedSource)> = context.sources.iter().collect();
    sources.sort_by(|left, right| left.0.module.cmp(&right.0.module));
    let mut found = Vec::new();
    for &&(source, parsed) in &sources {
        let declared = declared(context, &source.module);
        for (class, method, receiver) in instance_methods(parsed.module()) {
            let mut calls = Calls {
                receiver,
                found: Vec::new(),
            };
            for statement in &method.body {
                calls.visit_stmt(statement);
            }
            for (call, attributes) in calls.found {
                let assessed = evidence.assess(&source.module, class, &attributes, &declared);
                if assessed.unresolved_at.is_none() {
                    continue;
                }
                found.push(finding(
                    source,
                    parsed,
                    (class, method, receiver),
                    call,
                    &attributes,
                    &assessed,
                ));
            }
        }
    }
    found
}

fn declared(context: &StructureContext<'_>, module: &str) -> Declared {
    let Some(origin) = context.contracts.policy_for(module, "navigation") else {
        return Declared::default();
    };
    let names = |key: &str| -> HashSet<String> {
        origin
            .value
            .get(key)
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default()
    };
    Declared {
        namespaces: names("namespace_types"),
        factories: names("instance_factories"),
    }
}

fn finding(
    source: &SourceFile,
    parsed: &ParsedSource,
    (class, method, receiver): (&StmtClassDef, &StmtFunctionDef, &str),
    call: &ExprCall,
    attributes: &[&str],
    evidence: &Evidence,
) -> StructureFinding {
    let text = &parsed.text;
    let (line, column) = text.position(usize::from(call.start()));
    let (end_line, end_column) = text.position(usize::from(call.end()));
    let path = std::iter::once(receiver)
        .chain(attributes.iter().copied())
        .collect::<Vec<_>>()
        .join(".");
    let unresolved = evidence.unresolved_at.unwrap_or_default();
    let unresolved_path = std::iter::once(receiver)
        .chain(attributes[..unresolved].iter().copied())
        .collect::<Vec<_>>()
        .join(".");
    StructureFinding {
        message: format!(
            "review method call through {path}; it traverses {} instance attributes",
            attributes.len()
        ),
        path: source.path.clone(),
        position: Some((line, column + 1)),
        end_line: Some(end_line),
        end_column: Some(end_column + 1),
        source_module: Some(source.module.clone()),
        expected: json!({
            "class": class.name.as_str(),
            "method": method.name.as_str(),
            "callable": unparse(&text.text, &call.func),
            "stored_path": path,
            "stored_depth": attributes.len(),
            "automatic_rewrite": false,
            "review_required": true,
            "namespace_evidence": {
                "status": "unproven",
                "root_type": evidence.root_type,
                "unresolved_path": unresolved_path,
                "unresolved_type": evidence.unresolved_type,
            },
        }),
        hint: HINT.to_owned(),
        ..StructureFinding::default()
    }
}

/// Undecorated instance methods whose receiver is never rebound.
fn instance_methods(module: &ModModule) -> Vec<(&StmtClassDef, &StmtFunctionDef, &str)> {
    let mut found = Vec::new();
    for node in tree_nodes(module) {
        let TreeNode::Statement(Stmt::ClassDef(class)) = node else {
            continue;
        };
        for statement in &class.body {
            let Stmt::FunctionDef(method) = statement else {
                continue;
            };
            let parameters = &method.parameters;
            let Some(first) = parameters.posonlyargs.iter().chain(&parameters.args).next() else {
                continue;
            };
            if !method.decorator_list.is_empty() || IMPLICIT.contains(&method.name.as_str()) {
                continue;
            }
            let receiver = first.parameter.name.as_str();
            let mut rebinds = Rebinds {
                receiver,
                found: false,
            };
            for statement in &method.body {
                rebinds.visit_stmt(statement);
            }
            if !rebinds.found {
                found.push((class, method, receiver));
            }
        }
    }
    found
}

/// Whether a body binds the receiver name anywhere, nested scopes included.
struct Rebinds<'r> {
    receiver: &'r str,
    found: bool,
}

impl<'a> Visitor<'a> for Rebinds<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let binds = match statement {
            Stmt::FunctionDef(node) => node.name.as_str() == self.receiver,
            Stmt::ClassDef(node) => node.name.as_str() == self.receiver,
            Stmt::Import(node) => node.names.iter().any(|alias| bound(alias) == self.receiver),
            Stmt::ImportFrom(node) => node.names.iter().any(|alias| bound(alias) == self.receiver),
            _ => false,
        };
        self.found |= binds;
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(node) = expression
            && matches!(node.ctx, ExprContext::Store | ExprContext::Del)
            && node.id.as_str() == self.receiver
        {
            self.found = true;
        }
        visitor::walk_expr(self, expression);
    }

    fn visit_except_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        if node
            .name
            .as_ref()
            .is_some_and(|name| name.as_str() == self.receiver)
        {
            self.found = true;
        }
        visitor::walk_except_handler(self, handler);
    }

    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        let name = match pattern {
            Pattern::MatchAs(node) => node.name.as_ref(),
            Pattern::MatchStar(node) => node.name.as_ref(),
            Pattern::MatchMapping(node) => node.rest.as_ref(),
            _ => None,
        };
        if name.is_some_and(|name| name.as_str() == self.receiver) {
            self.found = true;
        }
        visitor::walk_pattern(self, pattern);
    }
}

/// The name an import alias binds.
fn bound(alias: &ruff_python_ast::Alias) -> &str {
    alias.asname.as_ref().map_or_else(
        || alias.name.split('.').next().unwrap_or_default(),
        |name| name.as_str(),
    )
}

/// Calls reached through two or more receiver attributes, outside nested scopes.
struct Calls<'s, 'r> {
    receiver: &'r str,
    found: Vec<(&'s ExprCall, Vec<&'s str>)>,
}

impl<'s> Calls<'s, '_> {
    fn expression(&mut self, expression: &'s Expr, in_receiver: bool) {
        match expression {
            Expr::Lambda(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::Generator(_) => {}
            Expr::Call(call) => {
                let attributes = stored_attributes(call, self.receiver);
                let stored = !attributes.is_empty();
                if stored && !in_receiver {
                    self.found.push((call, attributes));
                }
                self.expression(&call.func, in_receiver || stored);
                for argument in &call.arguments.args {
                    self.expression(argument, false);
                }
                for keyword in &call.arguments.keywords {
                    self.expression(&keyword.value, false);
                }
            }
            Expr::Attribute(node) => self.expression(&node.value, in_receiver),
            other => visitor::walk_expr(self, other),
        }
    }
}

impl<'s> Visitor<'s> for Calls<'s, '_> {
    fn visit_stmt(&mut self, statement: &'s Stmt) {
        if !matches!(statement, Stmt::FunctionDef(_) | Stmt::ClassDef(_)) {
            visitor::walk_stmt(self, statement);
        }
    }

    fn visit_expr(&mut self, expression: &'s Expr) {
        self.expression(expression, false);
    }
}

/// The receiver attributes before the first call of a chain, when there are two or more.
fn stored_attributes<'s>(call: &'s ExprCall, receiver: &str) -> Vec<&'s str> {
    let mut innermost = call;
    let mut current: &Expr = &call.func;
    loop {
        match current {
            Expr::Call(node) => {
                innermost = node;
                current = &node.func;
            }
            Expr::Attribute(node) => current = &node.value,
            _ => break,
        }
    }
    if !matches!(current, Expr::Name(name) if name.id.as_str() == receiver) {
        return Vec::new();
    }
    let Expr::Attribute(function) = innermost.func.as_ref() else {
        return Vec::new();
    };
    let mut attributes = Vec::new();
    let mut current: &Expr = &function.value;
    while let Expr::Attribute(node) = current {
        attributes.push(node.attr.as_str());
        current = &node.value;
    }
    if attributes.len() < 2 {
        return Vec::new();
    }
    attributes.reverse();
    attributes
}

/// Static evidence for data and module namespaces across the analyzed modules.
struct Namespaces<'a> {
    namespaces: HashMap<&'a str, Namespace>,
    trees: HashMap<&'a str, &'a ModModule>,
    definitions: HashMap<String, Option<(String, &'a StmtClassDef)>>,
    records: HashMap<String, bool>,
    owners: HashMap<usize, HashMap<String, Option<Type>>>,
}

impl<'a> Namespaces<'a> {
    fn assess(
        &mut self,
        module: &str,
        owner: &'a StmtClassDef,
        attributes: &[&str],
        declared: &Declared,
    ) -> Evidence {
        let key = std::ptr::from_ref(owner).addr();
        if !self.owners.contains_key(&key) {
            let fields = self.instance_fields(module, owner, &declared.factories);
            self.owners.insert(key, fields);
        }
        let mut value = self.owners[&key].get(attributes[0]).cloned().flatten();
        let root_type = value.as_ref().map(|value| value.name.clone());
        for (depth, attribute) in attributes.iter().enumerate().skip(1) {
            let current = match value {
                Some(current) if self.namespace(&current, &declared.namespaces) => current,
                other => {
                    return Evidence {
                        root_type,
                        unresolved_at: Some(depth),
                        unresolved_type: other.map(|value| value.name),
                    };
                }
            };
            value = if declared.namespaces.contains(&current.name) {
                Some(current)
            } else {
                self.attribute(&current, attribute, 0)
            };
        }
        match value {
            Some(value) if self.namespace(&value, &declared.namespaces) => Evidence {
                root_type,
                unresolved_at: None,
                unresolved_type: None,
            },
            other => Evidence {
                root_type,
                unresolved_at: Some(attributes.len()),
                unresolved_type: other.map(|value| value.name),
            },
        }
    }

    fn namespace(&mut self, value: &Type, declared: &HashSet<String>) -> bool {
        let name = value.name.as_str();
        VALUES.contains(&name)
            || declared.contains(name)
            || NAMESPACES.contains(&name)
            || self.record(name)
    }

    fn record(&mut self, name: &str) -> bool {
        if name == "pydantic.BaseModel" {
            return true;
        }
        if let Some(&known) = self.records.get(name) {
            return known;
        }
        self.records.insert(name.to_owned(), false);
        if let Some((module, node)) = self.definition(name) {
            let decorated = node.decorator_list.iter().any(|decorator| {
                let target = match &decorator.expression {
                    Expr::Call(call) => call.func.as_ref(),
                    other => other,
                };
                self.qualified(&module, target).as_deref() == Some("dataclasses.dataclass")
            });
            let record = decorated
                || node.bases().iter().any(|base| {
                    self.annotation(&module, base)
                        .is_some_and(|base| self.record(&base.name))
                });
            self.records.insert(name.to_owned(), record);
        }
        self.records[name]
    }

    fn attribute(&mut self, value: &Type, attribute: &str, depth: usize) -> Option<Type> {
        if depth > MAX_HOPS {
            return None;
        }
        if let Some(fields) = &value.fields {
            return fields.get(attribute).cloned().flatten();
        }
        let name = value.name.as_str();
        if name == "types.ModuleType" || (name.starts_with("pathlib.") && attribute == "parent") {
            return Some(value.clone());
        }
        if name == "subprocess.Popen" && matches!(attribute, "stdin" | "stdout" | "stderr") {
            return Some(Type::named("io.IOBase"));
        }
        let (module, node) = self.definition(name)?;
        for statement in &node.body {
            if let Stmt::AnnAssign(assignment) = statement
                && let Expr::Name(target) = assignment.target.as_ref()
                && target.id.as_str() == attribute
            {
                return self.annotation(&module, &assignment.annotation);
            }
        }
        for base in node.bases() {
            if let Some(base) = self.annotation(&module, base)
                && let Some(found) = self.attribute(&base, attribute, depth + 1)
            {
                return Some(found);
            }
        }
        None
    }

    fn definition(&mut self, name: &str) -> Option<(String, &'a StmtClassDef)> {
        if let Some(known) = self.definitions.get(name) {
            return known.clone();
        }
        let found = resolve_dotted(name, &self.namespaces).and_then(|(module, symbol)| {
            let symbol = symbol?;
            let tree = self.trees.get(module.as_str())?;
            tree.body.iter().find_map(|statement| match statement {
                Stmt::ClassDef(class) if class.name.as_str() == symbol => {
                    Some((module.clone(), class))
                }
                _ => None,
            })
        });
        self.definitions.insert(name.to_owned(), found.clone());
        found
    }

    fn instance_fields(
        &mut self,
        module: &str,
        owner: &StmtClassDef,
        factories: &HashSet<String>,
    ) -> HashMap<String, Option<Type>> {
        let mut declared: HashMap<String, Option<Type>> = HashMap::new();
        let mut inferred: HashMap<String, Option<Type>> = HashMap::new();
        for statement in &owner.body {
            if let Stmt::AnnAssign(assignment) = statement
                && let Expr::Name(target) = assignment.target.as_ref()
            {
                let annotation = self.annotation(module, &assignment.annotation);
                declared.insert(target.id.to_string(), annotation);
            }
            if let Stmt::Assign(assignment) = statement
                && let [Expr::Name(target)] = assignment.targets.as_slice()
                && let Some(made) = self.made(module, &assignment.value, factories)
            {
                // A class attribute holds an instance only when a declared factory made it.
                declared.insert(target.id.to_string(), Some(made));
            }
            let Stmt::FunctionDef(function) = statement else {
                continue;
            };
            let arguments = &function.parameters;
            let all: Vec<_> = arguments
                .posonlyargs
                .iter()
                .chain(&arguments.args)
                .chain(&arguments.kwonlyargs)
                .collect();
            let Some(first) = all.first() else {
                continue;
            };
            let receiver = first.parameter.name.as_str();
            let mut parameters: HashMap<&str, Option<Type>> = HashMap::new();
            for argument in &all {
                let annotation = argument
                    .parameter
                    .annotation
                    .as_ref()
                    .and_then(|annotation| self.annotation(module, annotation));
                parameters.insert(argument.parameter.name.as_str(), annotation);
            }
            let nodes = body_nodes(function);
            let shadowed: HashSet<&str> = nodes
                .iter()
                .filter_map(|node| match node {
                    TreeNode::Expression(Expr::Name(name))
                        if matches!(name.ctx, ExprContext::Store | ExprContext::Del) =>
                    {
                        Some(name.id.as_str())
                    }
                    _ => None,
                })
                .collect();
            if function.name.as_str() != "__init__"
                && let Some(returns) = &function.returns
                && function.decorator_list.iter().any(|decorator| {
                    matches!(
                        self.qualified(module, &decorator.expression).as_deref(),
                        Some("builtins.property" | "functools.cached_property")
                    )
                })
            {
                let annotation = self.annotation(module, returns);
                declared.insert(function.name.to_string(), annotation);
            }
            if !function.decorator_list.is_empty()
                || IMPLICIT.contains(&function.name.as_str())
                || shadowed.contains(receiver)
            {
                continue;
            }
            for node in &nodes {
                let TreeNode::Statement(statement) = node else {
                    continue;
                };
                let targets: &[Expr] = match statement {
                    Stmt::Assign(node) => &node.targets,
                    Stmt::AnnAssign(node) => std::slice::from_ref(node.target.as_ref()),
                    Stmt::AugAssign(node) => std::slice::from_ref(node.target.as_ref()),
                    _ => continue,
                };
                for target in targets {
                    let Expr::Attribute(target) = target else {
                        continue;
                    };
                    if !matches!(target.value.as_ref(), Expr::Name(name) if name.id.as_str() == receiver)
                    {
                        continue;
                    }
                    let attribute = target.attr.to_string();
                    match statement {
                        Stmt::AnnAssign(node) => {
                            let annotation = self.annotation(module, &node.annotation);
                            declared.insert(attribute, annotation);
                        }
                        Stmt::Assign(node) => {
                            let value = if function.name.as_str() == "__init__" {
                                self.expression(
                                    module,
                                    &node.value,
                                    (&parameters, &shadowed),
                                    factories,
                                )
                            } else {
                                None
                            };
                            let value = match inferred.get(&attribute) {
                                Some(known) if *known != value => None,
                                _ => value,
                            };
                            inferred.insert(attribute, value);
                        }
                        _ => {
                            inferred.insert(attribute, None);
                        }
                    }
                }
            }
        }
        inferred.extend(declared);
        inferred
    }

    fn expression(
        &mut self,
        module: &str,
        node: &Expr,
        (parameters, shadowed): (&HashMap<&str, Option<Type>>, &HashSet<&str>),
        factories: &HashSet<String>,
    ) -> Option<Type> {
        match node {
            Expr::Name(name) => {
                let name = name.id.as_str();
                if shadowed.contains(name) {
                    return None;
                }
                if let Some(parameter) = parameters.get(name) {
                    return parameter.clone();
                }
                self.imported_module(module, name)
                    .then(|| Type::named("types.ModuleType"))
            }
            Expr::Call(call) => {
                if names_in(&call.func)
                    .iter()
                    .any(|name| shadowed.contains(name) || parameters.contains_key(name))
                {
                    return None;
                }
                let qualified = self.qualified(module, &call.func);
                if qualified.as_deref() == Some("types.SimpleNamespace") {
                    if call
                        .arguments
                        .keywords
                        .iter()
                        .any(|keyword| keyword.arg.is_none())
                    {
                        return None;
                    }
                    let mut fields = HashMap::new();
                    for keyword in &call.arguments.keywords {
                        let Some(argument) = &keyword.arg else {
                            continue;
                        };
                        let value = self.expression(
                            module,
                            &keyword.value,
                            (parameters, shadowed),
                            &HashSet::new(),
                        );
                        fields.insert(argument.to_string(), value);
                    }
                    return Some(Type {
                        name: "types.SimpleNamespace".to_owned(),
                        fields: Some(fields),
                    });
                }
                self.made(module, node, factories)
                    .or_else(|| qualified.map(Type::named))
            }
            _ => None,
        }
    }

    /// Whether `name` is bound by a top-level `import` of the module itself.
    fn imported_module(&self, module: &str, name: &str) -> bool {
        let Some(tree) = self.trees.get(module) else {
            return false;
        };
        let Some(namespace) = self.namespaces.get(module) else {
            return false;
        };
        tree.body.iter().any(|statement| {
            let Stmt::Import(import) = statement else {
                return false;
            };
            import
                .names
                .iter()
                .filter(|alias| bound(alias) == name)
                .any(|alias| {
                    let target = if alias.asname.is_some() {
                        alias.name.as_str()
                    } else {
                        bound(alias)
                    };
                    namespace
                        .imported
                        .get(name)
                        .is_some_and(|imported| imported == target)
                })
        })
    }

    /// The class `factory(Class)` or `factory[T](Class)` makes an instance of.
    fn made(&self, module: &str, node: &Expr, factories: &HashSet<String>) -> Option<Type> {
        if factories.is_empty() {
            return None;
        }
        let Expr::Call(call) = node else {
            return None;
        };
        let first = call.arguments.args.first()?;
        let function = match call.func.as_ref() {
            Expr::Subscript(subscript) => subscript.value.as_ref(),
            other => other,
        };
        let qualified = self.qualified(module, function)?;
        if !factories.contains(&qualified) {
            return None;
        }
        self.annotation(module, first)
    }

    fn annotation(&self, module: &str, node: &Expr) -> Option<Type> {
        match node {
            Expr::StringLiteral(literal) => {
                let parsed = parse_eval(literal.value.to_str())?;
                self.annotation(module, parsed.expr())
            }
            Expr::BinOp(operation) => {
                if operation.op != Operator::BitOr {
                    return self.qualified_type(module, node);
                }
                if operation.right.is_none_literal_expr() {
                    return self.annotation(module, &operation.left);
                }
                if operation.left.is_none_literal_expr() {
                    return self.annotation(module, &operation.right);
                }
                None
            }
            Expr::Subscript(subscript) => {
                let origin = self.qualified(module, &subscript.value);
                if matches!(
                    origin.as_deref(),
                    Some("typing.Optional" | "typing.Annotated")
                ) {
                    let argument = match subscript.slice.as_ref() {
                        Expr::Tuple(tuple) => tuple.elts.first()?,
                        other => other,
                    };
                    return self.annotation(module, argument);
                }
                self.annotation(module, &subscript.value)
            }
            _ => self.qualified_type(module, node),
        }
    }

    fn qualified_type(&self, module: &str, node: &Expr) -> Option<Type> {
        let name = self.qualified(module, node)?;
        let name = match name.as_str() {
            "typing.List" => "builtins.list",
            "typing.Dict" => "builtins.dict",
            "typing.Set" => "builtins.set",
            "typing.FrozenSet" => "builtins.frozenset",
            "typing.Tuple" => "builtins.tuple",
            "typing.Mapping" => "collections.abc.Mapping",
            "typing.MutableMapping" => "collections.abc.MutableMapping",
            "typing.Sequence" => "collections.abc.Sequence",
            _ => return Some(Type::named(name)),
        };
        Some(Type::named(name))
    }

    fn qualified(&self, module: &str, node: &Expr) -> Option<String> {
        match node {
            Expr::Name(name) => {
                let namespace = self.namespaces.get(module)?;
                let name = name.id.as_str();
                if let Some(imported) = namespace.imported.get(name) {
                    return (!imported.is_empty()).then(|| imported.clone());
                }
                if namespace.defined.contains(name) {
                    return Some(format!("{module}.{name}"));
                }
                let builtin = format!("builtins.{name}");
                (VALUES.contains(&builtin.as_str()) || name == "property").then_some(builtin)
            }
            Expr::Attribute(attribute) => {
                let parent = self.qualified(module, &attribute.value)?;
                Some(format!("{parent}.{}", attribute.attr))
            }
            _ => None,
        }
    }
}

/// Every node of a method body outside nested functions, classes and lambdas.
fn body_nodes(method: &StmtFunctionDef) -> Vec<TreeNode<'_>> {
    let mut pending: Vec<TreeNode<'_>> = method.body.iter().map(TreeNode::Statement).collect();
    let mut found = Vec::new();
    while let Some(node) = pending.pop() {
        if matches!(
            node,
            TreeNode::Statement(Stmt::FunctionDef(_) | Stmt::ClassDef(_))
                | TreeNode::Expression(Expr::Lambda(_))
        ) {
            continue;
        }
        found.push(node);
        pending.extend(children(node));
    }
    found
}
