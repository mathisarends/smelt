//! Bounded class ownership and receiver facts for private attribute accesses, like
//! `smelt.analysis.private_attributes`.
//!
//! Python keeps member expressions as source text so its fact cache survives edits;
//! without a cache the facts keep the parsed expressions themselves.
use super::bindings::{BindingCounts, Coverage, qualified_name};
use super::namespace::{Namespace, resolve_export};
use super::nodes::{names_in, parse_eval};
use super::walk::{TreeNode, children};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    ExceptHandler, Expr, ExprAttribute, ExprContext, Operator, Pattern, Stmt, StmtClassDef,
    StmtFunctionDef,
};
use ruff_text_size::Ranged;
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

// Re-export chains are short; a longer one is more likely a cycle than a design.
const MAX_HOPS: usize = 8;

const RECEIVER_DECORATORS: [&str; 3] = [
    "builtins.property",
    "builtins.classmethod",
    "functools.cached_property",
];

const PROPERTIES: [&str; 2] = ["builtins.property", "functools.cached_property"];

pub struct PrivateAttributeAccess {
    pub attribute: String,
    pub line: usize,
    pub column: usize,
    pub end_line: usize,
    pub end_column: usize,
    pub owner_module: String,
    pub owner_class: String,
    pub receiver_type: String,
    pub operation: &'static str,
}

/// A receiver attribute a method assigns, with the source of its type evidence.
struct Field<'a> {
    attribute: &'a str,
    annotation: Option<&'a Expr>,
    // only recorded for `__init__`, the one method whose values type fields
    value: Option<&'a Expr>,
}

struct Method<'a> {
    name: &'a str,
    // qualified decorator names; unknown decorators are None
    decorators: Vec<Option<String>>,
    returns: Option<&'a Expr>,
    fields: Vec<Field<'a>>,
    // unwritten parameters and their annotations
    parameters: Vec<(&'a str, &'a Expr)>,
    // names written in the method that its field values mention
    blocked: Vec<&'a str>,
}

/// One class-body statement that can define members, in source order.
struct BodyFact<'a> {
    names: Vec<&'a str>,
    annotation: Option<&'a Expr>,
    value: Option<&'a Expr>,
    method: Option<Method<'a>>,
}

struct ClassFacts<'a> {
    dataclass_only: bool,
    // the only base, if the class has exactly one
    base: Option<&'a Expr>,
    body: Vec<BodyFact<'a>>,
}

struct ModuleFacts<'a> {
    bindings: HashMap<String, String>,
    // uniquely defined top-level classes
    classes: HashMap<&'a str, Rc<ClassFacts<'a>>>,
}

#[derive(Clone)]
struct Value {
    name: String,
    own_receiver: bool,
}

impl Value {
    fn named(name: String) -> Self {
        Self {
            name,
            own_receiver: false,
        }
    }
}

#[derive(Clone)]
struct Member {
    owner: String,
    type_name: Option<String>,
}

type Members = Rc<RefCell<HashMap<String, Member>>>;
type Env<'a> = HashMap<&'a str, Value>;

pub struct PrivateAttributeIndex<'a> {
    namespaces: &'a HashMap<&'a str, Namespace>,
    sources: HashMap<&'a str, (&'a SourceFile, &'a ParsedSource)>,
    facts: HashMap<String, Option<Rc<ModuleFacts<'a>>>>,
    classes: HashMap<String, Option<(String, Rc<ClassFacts<'a>>)>>,
    members: HashMap<String, Members>,
}

impl<'a> PrivateAttributeIndex<'a> {
    pub fn new(
        namespaces: &'a HashMap<&'a str, Namespace>,
        sources: &[(&'a SourceFile, &'a ParsedSource)],
    ) -> Self {
        Self {
            namespaces,
            sources: sources
                .iter()
                .map(|&(source, parsed)| (source.module.as_str(), (source, parsed)))
                .collect(),
            facts: HashMap::new(),
            classes: HashMap::new(),
            members: HashMap::new(),
        }
    }

    /// The private attribute accesses in `source` with a statically known owner.
    pub fn accesses(
        &mut self,
        source: &'a SourceFile,
        parsed: &'a ParsedSource,
    ) -> Vec<PrivateAttributeAccess> {
        let lines = private_lines(parsed);
        if lines.is_empty() {
            return Vec::new();
        }
        let mut scan = Scan {
            parsed,
            module: &source.module,
            lines: &lines,
            found: Vec::new(),
        };
        self.scope(&mut scan, &parsed.module().body, HashMap::new(), None, true);
        scan.found
    }

    fn scope(
        &mut self,
        scan: &mut Scan<'a, '_>,
        body: &'a [Stmt],
        mut env: Env<'a>,
        owner: Option<&'a StmtClassDef>,
        module_scope: bool,
    ) {
        let module = scan.module;
        let writes = writes(&scope_nodes(body));
        let mut blocked: HashSet<&str> =
            writes.keys().copied().chain(env.keys().copied()).collect();
        if module_scope && let Some(facts) = self.module_facts(module) {
            for name in facts.bindings.keys() {
                blocked.remove(name.as_str());
            }
        }
        for statement in body {
            let has_access = scan.may_access(statement);
            match statement {
                Stmt::ClassDef(class) => {
                    if has_access {
                        self.scope(scan, &class.body, HashMap::new(), Some(class), false);
                    }
                    continue;
                }
                Stmt::FunctionDef(function) => {
                    if has_access {
                        let parameters = self.function_env(module, function, owner);
                        self.scope(scan, &function.body, parameters, None, false);
                    }
                    continue;
                }
                _ => {
                    if has_access {
                        self.statement_accesses(scan, statement, &env, &blocked);
                    }
                }
            }
            for target in targets(statement) {
                let Expr::Name(name) = target else {
                    continue;
                };
                if writes.get(name.id.as_str()) != Some(&1) {
                    continue;
                }
                let value = match statement {
                    Stmt::AnnAssign(node) => {
                        self.annotation(module, &node.annotation).map(Value::named)
                    }
                    Stmt::Assign(node) => self.value(module, &node.value, &env, &blocked, 0),
                    _ => None,
                };
                if let Some(value) = value {
                    env.insert(name.id.as_str(), value);
                }
            }
        }
    }

    fn statement_accesses(
        &mut self,
        scan: &mut Scan<'a, '_>,
        statement: &'a Stmt,
        env: &Env<'a>,
        blocked: &HashSet<&str>,
    ) {
        let module = scan.module;
        let mut operations: Option<Operations> = None;
        for node in statement_nodes(statement) {
            let TreeNode::Expression(Expr::Attribute(node)) = node else {
                continue;
            };
            if !is_private(node.attr.as_str()) {
                continue;
            }
            let Some(receiver) = self.value(module, &node.value, env, blocked, 0) else {
                continue;
            };
            if receiver.own_receiver {
                continue;
            }
            let Some(member) = self.member(&receiver.name, node.attr.as_str(), 0) else {
                continue;
            };
            let operations = operations.get_or_insert_with(|| Operations::of(statement));
            let text = &scan.parsed.text;
            let (line, column) = text.position(usize::from(node.start()));
            let (end_line, end_column) = text.position(usize::from(node.end()));
            let owner_module = member
                .owner
                .rsplit_once('.')
                .map_or("", |(module, _)| module)
                .to_owned();
            scan.found.push(PrivateAttributeAccess {
                attribute: node.attr.to_string(),
                line,
                column: column + 1,
                end_line,
                end_column: end_column + 1,
                owner_module,
                owner_class: member.owner,
                receiver_type: receiver.name,
                operation: operations.of_node(node),
            });
        }
    }

    fn member(&mut self, name: &str, attribute: &str, depth: usize) -> Option<Member> {
        if depth >= MAX_HOPS {
            return None;
        }
        let (qualified, owner) = self.class(name)?;
        let module = parent(&qualified).to_owned();
        let members = self.members(&qualified, &owner);
        let found = members.borrow().get(attribute).cloned();
        if found.is_some() {
            return found;
        }
        // Do not guess a method resolution order across multiple or unknown bases.
        let base = self.annotation(&module, owner.base?)?;
        self.member(&base, attribute, depth + 1)
    }

    fn value(
        &mut self,
        module: &str,
        node: &Expr,
        env: &Env<'_>,
        blocked: &HashSet<&str>,
        depth: usize,
    ) -> Option<Value> {
        if depth >= MAX_HOPS {
            return None;
        }
        if let Expr::Name(name) = node
            && let Some(value) = env.get(name.id.as_str())
        {
            return Some(value.clone());
        }
        let candidate = match node {
            Expr::Call(call) => call.func.as_ref(),
            other => other,
        };
        if !names_in(candidate)
            .iter()
            .any(|name| blocked.contains(name))
            && let Some(name) = self.annotation(module, candidate)
        {
            return Some(Value::named(name));
        }
        let Expr::Attribute(attribute) = node else {
            return None;
        };
        let receiver = self.value(module, &attribute.value, env, blocked, depth + 1)?;
        let member = self.member(&receiver.name, attribute.attr.as_str(), 0)?;
        member.type_name.map(Value::named)
    }

    fn members(&mut self, name: &str, owner: &ClassFacts<'a>) -> Members {
        if let Some(members) = self.members.get(name) {
            return Rc::clone(members);
        }
        // Registered before resolving so that cyclic lookups see the partial map.
        let members: Members = Rc::default();
        self.members.insert(name.to_owned(), Rc::clone(&members));
        self.resolve_members(name, owner, &members);
        members
    }

    fn resolve_members(&mut self, name: &str, owner: &ClassFacts<'a>, members: &Members) {
        let module = parent(name).to_owned();
        let record = |attribute: &str, type_name: Option<String>| {
            let mut members = members.borrow_mut();
            let type_name = match members.get(attribute) {
                Some(previous) if previous.type_name != type_name => None,
                _ => type_name,
            };
            members.insert(
                attribute.to_owned(),
                Member {
                    owner: name.to_owned(),
                    type_name,
                },
            );
        };
        for statement in &owner.body {
            if let Some(method) = &statement.method {
                let returned = match method.returns {
                    Some(returns)
                        if method.decorators.iter().any(|name| {
                            name.as_deref()
                                .is_some_and(|name| PROPERTIES.contains(&name))
                        }) =>
                    {
                        self.annotation(&module, returns)
                    }
                    _ => None,
                };
                record(method.name, returned);
                let mut parameters: Env<'_> = HashMap::new();
                for &(argument, annotation) in &method.parameters {
                    if let Some(name) = self.annotation(&module, annotation) {
                        parameters.insert(argument, Value::named(name));
                    }
                }
                let blocked: HashSet<&str> = method
                    .blocked
                    .iter()
                    .copied()
                    .chain(parameters.keys().copied())
                    .collect();
                for field in &method.fields {
                    let value_type = if let Some(annotation) = field.annotation {
                        self.annotation(&module, annotation)
                    } else if let Some(value) = field.value {
                        self.value(&module, value, &parameters, &blocked, 0)
                            .map(|value| value.name)
                    } else {
                        None
                    };
                    record(field.attribute, value_type);
                }
            } else if let Some(annotation) = statement.annotation {
                let annotated = self.annotation(&module, annotation);
                record(statement.names[0], annotated);
            } else if let Some(value) = statement.value {
                for target in &statement.names {
                    let value = self.value(&module, value, &HashMap::new(), &HashSet::new(), 0);
                    record(target, value.map(|value| value.name));
                }
            }
        }
    }

    fn annotation(&mut self, module: &str, node: &Expr) -> Option<String> {
        let facts = self.module_facts(module);
        let empty = HashMap::new();
        let bindings = facts.as_ref().map_or(&empty, |facts| &facts.bindings);
        match node {
            Expr::StringLiteral(literal) => {
                let parsed = parse_eval(literal.value.to_str())?;
                return self.annotation(module, parsed.expr());
            }
            Expr::Subscript(subscript)
                if matches!(
                    qualified_name(&subscript.value, bindings).as_deref(),
                    Some("typing.Annotated" | "typing.Optional" | "typing.ClassVar")
                ) =>
            {
                let argument = match subscript.slice.as_ref() {
                    Expr::Tuple(tuple) => tuple.elts.first()?,
                    other => other,
                };
                return self.annotation(module, argument);
            }
            Expr::BinOp(operation) if operation.op == Operator::BitOr => {
                if operation.right.is_none_literal_expr() {
                    return self.annotation(module, &operation.left);
                }
                if operation.left.is_none_literal_expr() {
                    return self.annotation(module, &operation.right);
                }
            }
            _ => {}
        }
        let name = qualified_name(node, bindings)?;
        self.class(&name).map(|(qualified, _)| qualified)
    }

    /// The qualified name and facts of the class `name` resolves to.
    fn class(&mut self, name: &str) -> Option<(String, Rc<ClassFacts<'a>>)> {
        if let Some(known) = self.classes.get(name) {
            return known.clone();
        }
        self.classes.insert(name.to_owned(), None);
        let definition = self.resolve_class(name);
        self.classes.insert(name.to_owned(), definition.clone());
        definition
    }

    fn resolve_class(&mut self, name: &str) -> Option<(String, Rc<ClassFacts<'a>>)> {
        let (prefix, last) = name.rsplit_once('.').unwrap_or(("", name));
        if !self.namespaces.contains_key(prefix) {
            return None;
        }
        let (module, symbol) = resolve_export(prefix, last, self.namespaces)?;
        let symbol = symbol?;
        let facts = self.module_facts(&module)?;
        let qualified = format!("{module}.{symbol}");
        if facts.bindings.get(&symbol) != Some(&qualified) {
            return None;
        }
        let owner = facts.classes.get(symbol.as_str())?;
        owner.dataclass_only.then(|| (qualified, Rc::clone(owner)))
    }

    fn module_facts(&mut self, module: &str) -> Option<Rc<ModuleFacts<'a>>> {
        if let Some(known) = self.facts.get(module) {
            return known.clone();
        }
        let facts = self.sources.get(module).and_then(|&(source, parsed)| {
            let namespace = self.namespaces.get(source.module.as_str())?;
            Some(Rc::new(module_facts(source, parsed, namespace)))
        });
        self.facts.insert(module.to_owned(), facts.clone());
        facts
    }

    fn function_env(
        &mut self,
        module: &str,
        function: &'a StmtFunctionDef,
        owner: Option<&StmtClassDef>,
    ) -> Env<'a> {
        let writes = writes(&scope_nodes(&function.body));
        let arguments = &function.parameters;
        let mut parameters: Env<'a> = HashMap::new();
        for argument in arguments
            .posonlyargs
            .iter()
            .chain(&arguments.args)
            .chain(&arguments.kwonlyargs)
        {
            let name = argument.parameter.name.as_str();
            if writes.contains_key(name) {
                continue;
            }
            let Some(annotation) = &argument.parameter.annotation else {
                continue;
            };
            if let Some(type_name) = self.annotation(module, annotation) {
                parameters.insert(name, Value::named(type_name));
            }
        }
        let first = arguments.posonlyargs.iter().chain(&arguments.args).next();
        let facts = self.module_facts(module);
        let empty = HashMap::new();
        let bindings = facts.as_ref().map_or(&empty, |facts| &facts.bindings);
        let receiver_decorators = function.decorator_list.iter().all(|decorator| {
            qualified_name(&decorator.expression, bindings)
                .is_some_and(|name| RECEIVER_DECORATORS.contains(&name.as_str()))
        });
        if let Some(owner) = owner
            && let Some(first) = first
            && !writes.contains_key(first.parameter.name.as_str())
            && receiver_decorators
        {
            parameters.insert(
                first.parameter.name.as_str(),
                Value {
                    name: format!("{module}.{}", owner.name),
                    own_receiver: true,
                },
            );
        }
        parameters
    }
}

/// The module being scanned, and the accesses found in it so far.
struct Scan<'a, 'm> {
    parsed: &'a ParsedSource,
    module: &'m str,
    // sorted lines with a private attribute node, to skip scopes without one
    lines: &'m [usize],
    found: Vec<PrivateAttributeAccess>,
}

impl Scan<'_, '_> {
    fn may_access(&self, statement: &Stmt) -> bool {
        let text = &self.parsed.text;
        let first = text.line_at(usize::from(statement.start()));
        let last = text.line_at(usize::from(statement.end()));
        let index = self.lines.partition_point(|&line| line < first);
        index < self.lines.len() && self.lines[index] <= last
    }
}

/// How each attribute node of one statement is used.
struct Operations {
    calls: HashSet<usize>,
    updates: HashSet<usize>,
}

impl Operations {
    fn of(statement: &Stmt) -> Self {
        struct Collector {
            operations: Operations,
        }
        impl<'b> Visitor<'b> for Collector {
            fn visit_stmt(&mut self, statement: &'b Stmt) {
                if let Stmt::AugAssign(node) = statement
                    && let Expr::Attribute(target) = node.target.as_ref()
                {
                    self.operations.updates.insert(address(target));
                }
                visitor::walk_stmt(self, statement);
            }

            fn visit_expr(&mut self, expression: &'b Expr) {
                if let Expr::Call(call) = expression
                    && let Expr::Attribute(function) = call.func.as_ref()
                {
                    self.operations.calls.insert(address(function));
                }
                visitor::walk_expr(self, expression);
            }
        }
        let mut collector = Collector {
            operations: Self {
                calls: HashSet::new(),
                updates: HashSet::new(),
            },
        };
        collector.visit_stmt(statement);
        collector.operations
    }

    fn of_node(&self, node: &ExprAttribute) -> &'static str {
        if self.calls.contains(&address(node)) {
            return "call";
        }
        if self.updates.contains(&address(node)) {
            return "update";
        }
        match node.ctx {
            ExprContext::Store => "write",
            ExprContext::Del => "delete",
            _ => "read",
        }
    }
}

fn address(node: &ExprAttribute) -> usize {
    std::ptr::from_ref(node).addr()
}

fn module_facts<'a>(
    source: &SourceFile,
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
    for name in ["property", "classmethod", "staticmethod"] {
        if counts.get(name) == 0 {
            bindings.insert(name.to_owned(), format!("builtins.{name}"));
        }
    }
    let classes: Vec<&StmtClassDef> = tree
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::ClassDef(class) => Some(class),
            _ => None,
        })
        .collect();
    let mut defined: HashMap<&str, usize> = HashMap::new();
    for class in &classes {
        *defined.entry(class.name.as_str()).or_insert(0) += 1;
    }
    let classes = classes
        .into_iter()
        .filter(|class| defined[class.name.as_str()] == 1)
        .map(|class| (class.name.as_str(), Rc::new(class_facts(class, &bindings))))
        .collect();
    ModuleFacts { bindings, classes }
}

fn class_facts<'a>(node: &'a StmtClassDef, bindings: &HashMap<String, String>) -> ClassFacts<'a> {
    let mut body = Vec::new();
    for statement in &node.body {
        match statement {
            Stmt::AnnAssign(assignment) => {
                if let Expr::Name(target) = assignment.target.as_ref() {
                    body.push(BodyFact {
                        names: vec![target.id.as_str()],
                        annotation: Some(&assignment.annotation),
                        value: None,
                        method: None,
                    });
                }
            }
            Stmt::Assign(assignment) => {
                let names: Vec<&str> = assignment
                    .targets
                    .iter()
                    .filter_map(|target| match target {
                        Expr::Name(name) => Some(name.id.as_str()),
                        _ => None,
                    })
                    .collect();
                if !names.is_empty() {
                    body.push(BodyFact {
                        names,
                        annotation: None,
                        value: Some(&assignment.value),
                        method: None,
                    });
                }
            }
            Stmt::FunctionDef(method) => body.push(BodyFact {
                names: Vec::new(),
                annotation: None,
                value: None,
                method: Some(method_facts(method, bindings)),
            }),
            _ => {}
        }
    }
    let bases = node.bases();
    ClassFacts {
        dataclass_only: node.decorator_list.iter().all(|decorator| {
            let target = match &decorator.expression {
                Expr::Call(call) => call.func.as_ref(),
                other => other,
            };
            qualified_name(target, bindings).as_deref() == Some("dataclasses.dataclass")
        }),
        base: (bases.len() == 1).then(|| &bases[0]),
        body,
    }
}

fn method_facts<'a>(method: &'a StmtFunctionDef, bindings: &HashMap<String, String>) -> Method<'a> {
    let decorators: Vec<Option<String>> = method
        .decorator_list
        .iter()
        .map(|decorator| qualified_name(&decorator.expression, bindings))
        .collect();
    let mut facts = Method {
        name: method.name.as_str(),
        decorators,
        returns: method.returns.as_deref(),
        fields: Vec::new(),
        parameters: Vec::new(),
        blocked: Vec::new(),
    };
    let arguments = &method.parameters;
    let Some(first) = arguments.posonlyargs.iter().chain(&arguments.args).next() else {
        return facts;
    };
    if facts.decorators.iter().any(|name| {
        !name
            .as_deref()
            .is_some_and(|name| RECEIVER_DECORATORS.contains(&name))
    }) {
        return facts;
    }
    let receiver = first.parameter.name.as_str();
    let nodes = scope_nodes(&method.body);
    let writes = writes(&nodes);
    if writes.contains_key(receiver) {
        return facts;
    }
    let mut mentioned: HashSet<&str> = HashSet::new();
    for node in &nodes {
        let TreeNode::Statement(statement) = node else {
            continue;
        };
        for target in targets(statement) {
            let Expr::Attribute(target) = target else {
                continue;
            };
            if !matches!(target.value.as_ref(), Expr::Name(name) if name.id.as_str() == receiver) {
                continue;
            }
            let attribute = target.attr.as_str();
            let field = match statement {
                Stmt::AnnAssign(node) => Field {
                    attribute,
                    annotation: Some(&node.annotation),
                    value: None,
                },
                Stmt::Assign(node) if facts.name == "__init__" => {
                    mentioned.extend(names_in(&node.value));
                    Field {
                        attribute,
                        annotation: None,
                        value: Some(&node.value),
                    }
                }
                _ => Field {
                    attribute,
                    annotation: None,
                    value: None,
                },
            };
            facts.fields.push(field);
        }
    }
    for argument in arguments
        .posonlyargs
        .iter()
        .chain(&arguments.args)
        .chain(&arguments.kwonlyargs)
    {
        let name = argument.parameter.name.as_str();
        if let Some(annotation) = &argument.parameter.annotation
            && !writes.contains_key(name)
        {
            facts.parameters.push((name, annotation));
        }
    }
    facts.blocked = mentioned
        .into_iter()
        .filter(|name| writes.contains_key(name))
        .collect();
    facts
}

fn is_private(name: &str) -> bool {
    name.starts_with('_') && !(name.starts_with("__") && name.ends_with("__"))
}

fn parent(name: &str) -> &str {
    name.rsplit_once('.').map_or("", |(parent, _)| parent)
}

/// Sorted lines holding a private, non-dunder attribute node.
fn private_lines(parsed: &ParsedSource) -> Vec<usize> {
    struct Lines<'p> {
        parsed: &'p ParsedSource,
        lines: Vec<usize>,
    }
    impl<'b> Visitor<'b> for Lines<'_> {
        fn visit_expr(&mut self, expression: &'b Expr) {
            if let Expr::Attribute(node) = expression
                && is_private(node.attr.as_str())
            {
                let line = self.parsed.text.line_at(usize::from(node.start()));
                self.lines.push(line);
            }
            visitor::walk_expr(self, expression);
        }
    }
    let mut collector = Lines {
        parsed,
        lines: Vec::new(),
    };
    collector.visit_body(&parsed.module().body);
    collector.lines.sort_unstable();
    collector.lines.dedup();
    collector.lines
}

/// The nodes of a body outside nested scopes, last statement first.
fn scope_nodes(body: &[Stmt]) -> Vec<TreeNode<'_>> {
    body.iter().rev().flat_map(statement_nodes).collect()
}

/// A statement's nodes outside nested scopes; nested scope nodes themselves included.
fn statement_nodes(statement: &Stmt) -> Vec<TreeNode<'_>> {
    let mut found = Vec::new();
    let mut pending = vec![TreeNode::Statement(statement)];
    while let Some(node) = pending.pop() {
        found.push(node);
        let nested = matches!(
            node,
            TreeNode::Statement(Stmt::FunctionDef(_) | Stmt::ClassDef(_))
                | TreeNode::Expression(
                    Expr::Lambda(_)
                        | Expr::ListComp(_)
                        | Expr::SetComp(_)
                        | Expr::DictComp(_)
                        | Expr::Generator(_)
                )
        );
        if !nested {
            pending.extend(children(node));
        }
    }
    found
}

fn writes<'a>(nodes: &[TreeNode<'a>]) -> HashMap<&'a str, usize> {
    let mut result: HashMap<&str, usize> = HashMap::new();
    let mut add = |name: &'a str| *result.entry(name).or_insert(0) += 1;
    for node in nodes {
        match node {
            TreeNode::Expression(Expr::Name(name)) if name.ctx != ExprContext::Load => {
                add(name.id.as_str());
            }
            TreeNode::Statement(Stmt::FunctionDef(node)) => add(node.name.as_str()),
            TreeNode::Statement(Stmt::ClassDef(node)) => add(node.name.as_str()),
            TreeNode::Statement(Stmt::Import(node)) => {
                for alias in &node.names {
                    add(alias.asname.as_ref().map_or_else(
                        || alias.name.split('.').next().unwrap_or_default(),
                        |name| name.as_str(),
                    ));
                }
            }
            TreeNode::Statement(Stmt::ImportFrom(node)) => {
                for alias in &node.names {
                    add(alias.asname.as_ref().map_or_else(
                        || alias.name.split('.').next().unwrap_or_default(),
                        |name| name.as_str(),
                    ));
                }
            }
            TreeNode::Statement(Stmt::Global(node)) => {
                for name in &node.names {
                    add(name.as_str());
                }
            }
            TreeNode::Statement(Stmt::Nonlocal(node)) => {
                for name in &node.names {
                    add(name.as_str());
                }
            }
            TreeNode::Handler(ExceptHandler::ExceptHandler(handler)) => {
                if let Some(name) = &handler.name {
                    add(name.as_str());
                }
            }
            TreeNode::Pattern(Pattern::MatchAs(pattern)) => {
                if let Some(name) = &pattern.name {
                    add(name.as_str());
                }
            }
            TreeNode::Pattern(Pattern::MatchStar(pattern)) => {
                if let Some(name) = &pattern.name {
                    add(name.as_str());
                }
            }
            TreeNode::Pattern(Pattern::MatchMapping(pattern)) => {
                if let Some(rest) = &pattern.rest {
                    add(rest.as_str());
                }
            }
            _ => {}
        }
    }
    result
}

fn targets(statement: &Stmt) -> &[Expr] {
    match statement {
        Stmt::Assign(node) => &node.targets,
        Stmt::Delete(node) => &node.targets,
        Stmt::AnnAssign(node) => std::slice::from_ref(node.target.as_ref()),
        Stmt::AugAssign(node) => std::slice::from_ref(node.target.as_ref()),
        _ => &[],
    }
}
