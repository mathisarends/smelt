//! IDM008: `idioms/loop-invariant-collection`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{
    BindingCounts, Coverage, qualified_name, root_name, unshadowed_bindings,
};
use crate::checker::analysis::dump::{dump_expr, dump_expr_renamed};
use crate::checker::analysis::nodes::{is_constant, parse_eval};
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    CmpOp, ElifElseClause, Expr, ExprContext, ExprSetComp, ModModule, Number, Stmt, StmtClassDef,
    StmtFor, StmtFunctionDef,
};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{HashMap, HashSet};

const SCALARS: [&str; 6] = [
    "builtins.str",
    "builtins.bytes",
    "builtins.int",
    "builtins.float",
    "builtins.bool",
    "builtins.NoneType",
];
const BUILTINS: [&str; 9] = [
    "str",
    "bytes",
    "int",
    "float",
    "bool",
    "tuple",
    "ValueError",
    "TypeError",
    "RuntimeError",
];
const ERRORS: [&str; 3] = [
    "builtins.ValueError",
    "builtins.TypeError",
    "builtins.RuntimeError",
];
const SET: &str = "builtins.set";
const HINT: &str =
    "Reuse one lookup set while preserving empty-loop behavior, guards and evaluation order.";

type Bindings = HashMap<String, String>;
type Environment<'a> = HashMap<&'a str, Shape>;

/// What a value is known to be: a scalar, a set, a local record or a tuple of one.
#[derive(Clone, PartialEq)]
enum Shape {
    Named(String),
    Tuple(Box<Shape>),
}

impl Shape {
    fn named(name: &str) -> Self {
        Self::Named(name.to_owned())
    }

    fn is_scalar(&self) -> bool {
        matches!(self, Self::Named(name) if SCALARS.contains(&name.as_str()))
    }

    fn is_passive(&self) -> bool {
        match self {
            Self::Tuple(item) => item.is_passive(),
            Self::Named(name) => SCALARS.contains(&name.as_str()) || name == SET,
        }
    }
}

/// A set comprehension on the right of `in` or `not in`.
#[derive(Clone, Copy)]
struct Site<'a> {
    expression: &'a Expr,
    comprehension: &'a ExprSetComp,
}

struct Facts<'a> {
    records: HashMap<&'a str, HashMap<&'a str, &'a Expr>>,
    bindings: Bindings,
}

impl<'a> Facts<'a> {
    fn annotation(&self, node: &Expr) -> Option<Shape> {
        if let Expr::StringLiteral(item) = node {
            let parsed = parse_eval(item.value.to_str())?;
            return self.parsed_annotation(parsed.expr());
        }
        self.parsed_annotation(node)
    }

    fn parsed_annotation(&self, node: &Expr) -> Option<Shape> {
        if let Expr::Name(item) = node
            && self.records.contains_key(item.id.as_str())
        {
            return Some(Shape::named(item.id.as_str()));
        }
        if let Some(target) = qualified_name(node, &self.bindings)
            && SCALARS.contains(&target.as_str())
        {
            return Some(Shape::Named(target));
        }
        let Expr::Subscript(subscript) = node else {
            return None;
        };
        let outer = qualified_name(&subscript.value, &self.bindings)?;
        if outer != "builtins.tuple" && outer != "typing.Tuple" {
            return None;
        }
        let Expr::Tuple(parts) = subscript.slice.as_ref() else {
            return None;
        };
        let [item, Expr::EllipsisLiteral(_)] = parts.elts.as_slice() else {
            return None;
        };
        self.annotation(item)
            .map(|item| Shape::Tuple(Box::new(item)))
    }

    fn value(&self, node: &'a Expr, env: &Environment<'a>) -> Option<Shape> {
        match node {
            Expr::StringLiteral(_) => Some(Shape::named("builtins.str")),
            Expr::BytesLiteral(_) => Some(Shape::named("builtins.bytes")),
            Expr::BooleanLiteral(_) => Some(Shape::named("builtins.bool")),
            Expr::NoneLiteral(_) => Some(Shape::named("builtins.NoneType")),
            Expr::NumberLiteral(item) => match item.value {
                Number::Int(_) => Some(Shape::named("builtins.int")),
                Number::Float(_) => Some(Shape::named("builtins.float")),
                Number::Complex { .. } => None,
            },
            Expr::Name(item) => env.get(item.id.as_str()).cloned(),
            Expr::Attribute(item) => {
                let Shape::Named(owner) = self.value(&item.value, env)? else {
                    return None;
                };
                let field = self.records.get(owner.as_str())?.get(item.attr.as_str())?;
                self.annotation(field)
            }
            Expr::Tuple(item) => {
                let mut kinds = item.elts.iter().map(|part| self.value(part, env));
                let first = kinds.next()??;
                kinds
                    .all(|kind| kind.as_ref() == Some(&first))
                    .then(|| Shape::Tuple(Box::new(first)))
            }
            Expr::Subscript(item) if is_int_constant(&item.slice) => {
                match self.value(&item.value, env)? {
                    Shape::Tuple(element) => Some(*element),
                    Shape::Named(_) => None,
                }
            }
            Expr::SetComp(item) => self.lookup(item, env).then(|| Shape::named(SET)),
            Expr::Compare(item) => item
                .operands
                .iter()
                .all(|part| self.is_passive(part, env))
                .then(|| Shape::named("builtins.bool")),
            Expr::BoolOp(item) => item
                .values
                .iter()
                .all(|part| self.is_passive(part, env))
                .then(|| Shape::named("builtins.bool")),
            Expr::UnaryOp(item) => {
                let kind = self.value(&item.operand, env)?;
                kind.is_scalar().then_some(kind)
            }
            Expr::BinOp(item) => {
                let left = self.value(&item.left, env)?;
                let right = self.value(&item.right, env)?;
                (left.is_scalar() && right.is_scalar()).then_some(left)
            }
            _ => None,
        }
    }

    fn is_passive(&self, node: &'a Expr, env: &Environment<'a>) -> bool {
        self.value(node, env).is_some_and(|kind| kind.is_passive())
    }

    fn lookup(&self, node: &'a ExprSetComp, env: &Environment<'a>) -> bool {
        let [generator] = node.generators.as_slice() else {
            return false;
        };
        if generator.is_async || !generator.ifs.is_empty() {
            return false;
        }
        let Expr::Name(target) = &generator.target else {
            return false;
        };
        let Some(Shape::Tuple(item)) = self.value(&generator.iter, env) else {
            return false;
        };
        let mut inner = env.clone();
        inner.insert(target.id.as_str(), *item);
        self.value(&node.elt, &inner)
            .is_some_and(|kind| kind.is_scalar())
    }
}

fn is_int_constant(node: &Expr) -> bool {
    match node {
        Expr::BooleanLiteral(_) => true,
        Expr::NumberLiteral(item) => matches!(item.value, Number::Int(_)),
        _ => false,
    }
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let source = input.parsed.text.text.as_str();
    let bindings = unshadowed_bindings(source, module, &BUILTINS);
    let facts = Facts {
        records: records(module, &bindings),
        bindings,
    };
    let mut found = Vec::new();
    for (function, owner) in functions(module) {
        for (loop_node, sites) in function_sites(source, function, owner, &facts) {
            found.push(finding(input, function, owner, loop_node, &sites));
        }
    }
    found
}

fn finding(
    input: &RuleInput<'_>,
    function: &StmtFunctionDef,
    owner: Option<&str>,
    loop_node: &StmtFor,
    sites: &[Site<'_>],
) -> Violation {
    let source = input.parsed.text.text.as_str();
    let first = sites[0];
    let expression = unparse(source, first.expression);
    let (line, column) = input.place(first.expression.start());
    let places: Vec<_> = sites
        .iter()
        .map(|site| {
            let (line, column) = input.place(site.expression.start());
            json!({"line": line, "column": column})
        })
        .collect();
    let edge = format!(
        "{}.{}: {}",
        owner.unwrap_or("<module>"),
        function.name,
        lookup_key(source, first.comprehension)
    );
    input.violation(Finding {
        message: format!(
            "Lookup set {expression} is rebuilt on each iteration from stable immutable inputs; review computing it once"
        ),
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({
            "function": function.name.as_str(),
            "loop_line": input.place(loop_node.start()).0,
            "expression": expression,
            "sites": places,
            "lookup_edges": [edge],
            "automatic_rewrite": false,
        }),
        hint: HINT.to_owned(),
        fix: None,
    })
}

fn functions(module: &ModModule) -> Vec<(&StmtFunctionDef, Option<&str>)> {
    let mut found = Vec::new();
    for statement in &module.body {
        match statement {
            Stmt::FunctionDef(function) => found.push((function, None)),
            Stmt::ClassDef(owner) => {
                for member in &owner.body {
                    if let Stmt::FunctionDef(method) = member {
                        found.push((method, Some(owner.name.as_str())));
                    }
                }
            }
            _ => {}
        }
    }
    found
}

fn function_sites<'a>(
    source: &str,
    function: &'a StmtFunctionDef,
    owner: Option<&str>,
    facts: &Facts<'a>,
) -> Vec<(&'a StmtFor, Vec<Site<'a>>)> {
    let mut found = Vec::new();
    if !function.decorator_list.is_empty() || suspends(function) {
        return found;
    }
    let mut env = Environment::new();
    let parameters = &function.parameters;
    let arguments: Vec<_> = parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .chain(&parameters.kwonlyargs)
        .map(|argument| &argument.parameter)
        .collect();
    for argument in &arguments {
        if let Some(annotation) = argument.annotation()
            && let Some(kind) = facts.annotation(annotation)
        {
            env.insert(argument.name.as_str(), kind);
        }
    }
    if let Some(owner) = owner
        && facts.records.contains_key(owner)
        && let Some(first) = arguments.first()
    {
        env.insert(first.name.as_str(), Shape::named(owner));
    }
    for statement in &function.body {
        if let Stmt::For(item) = statement
            && !item.is_async
        {
            for sites in loop_sites(source, statement, item, &env, facts) {
                found.push((item, sites));
            }
        }
        if !assignment(statement, &mut env, facts) {
            for name in writes(statement) {
                env.remove(name);
            }
        }
    }
    found
}

fn loop_sites<'a>(
    source: &str,
    statement: &'a Stmt,
    item: &'a StmtFor,
    env: &Environment<'a>,
    facts: &Facts<'a>,
) -> Vec<Vec<Site<'a>>> {
    let Expr::Name(target) = item.target.as_ref() else {
        return Vec::new();
    };
    let Some(Shape::Tuple(element)) = facts.value(&item.iter, env) else {
        return Vec::new();
    };
    let mut local = env.clone();
    local.insert(target.id.as_str(), *element);
    let mut candidates = Vec::new();
    if !body(&item.body, &mut local, facts, &mut candidates) {
        return Vec::new();
    }
    let written = writes(statement);
    let mut positions: HashMap<String, usize> = HashMap::new();
    let mut groups: Vec<Vec<Site<'a>>> = Vec::new();
    for candidate in candidates {
        if dependencies(candidate.comprehension).is_disjoint(&written) {
            let index = *positions
                .entry(lookup_key(source, candidate.comprehension))
                .or_insert_with(|| {
                    groups.push(Vec::new());
                    groups.len() - 1
                });
            groups[index].push(candidate);
        }
    }
    for sites in &mut groups {
        sites.sort_by_key(|site| site.expression.start());
    }
    groups
}

fn body<'a>(
    statements: &'a [Stmt],
    env: &mut Environment<'a>,
    facts: &Facts<'a>,
    found: &mut Vec<Site<'a>>,
) -> bool {
    statements
        .iter()
        .all(|statement| passive_statement(statement, env, facts, found))
}

fn passive_statement<'a>(
    statement: &'a Stmt,
    env: &mut Environment<'a>,
    facts: &Facts<'a>,
    found: &mut Vec<Site<'a>>,
) -> bool {
    match statement {
        Stmt::If(item) => branch(
            &item.test,
            &item.body,
            &item.elif_else_clauses,
            env,
            facts,
            found,
        ),
        Stmt::Assign(item) => {
            expression(&item.value, env, facts, found) && assignment(statement, env, facts)
        }
        Stmt::AnnAssign(item) => {
            item.value
                .as_ref()
                .is_some_and(|value| expression(value, env, facts, found))
                && assignment(statement, env, facts)
        }
        Stmt::Raise(item) => {
            item.cause.is_none()
                && item
                    .exc
                    .as_ref()
                    .is_some_and(|error| literal_error(error, &facts.bindings))
        }
        Stmt::Return(item) => item
            .value
            .as_ref()
            .is_none_or(|value| expression(value, env, facts, found)),
        Stmt::Pass(_) | Stmt::Break(_) | Stmt::Continue(_) => true,
        _ => false,
    }
}

/// One `if` or `elif` and everything after it, which CPython nests in `orelse`.
fn branch<'a>(
    test: &'a Expr,
    then: &'a [Stmt],
    rest: &'a [ElifElseClause],
    env: &mut Environment<'a>,
    facts: &Facts<'a>,
    found: &mut Vec<Site<'a>>,
) -> bool {
    let mut yes = env.clone();
    let mut no = env.clone();
    if !expression(test, env, facts, found)
        || !body(then, &mut yes, facts, found)
        || !otherwise(rest, &mut no, facts, found)
    {
        return false;
    }
    yes.retain(|name, kind| no.get(name) == Some(kind));
    *env = yes;
    true
}

fn otherwise<'a>(
    rest: &'a [ElifElseClause],
    env: &mut Environment<'a>,
    facts: &Facts<'a>,
    found: &mut Vec<Site<'a>>,
) -> bool {
    let Some((clause, after)) = rest.split_first() else {
        return true;
    };
    match &clause.test {
        Some(test) => branch(test, &clause.body, after, env, facts, found),
        None => body(&clause.body, env, facts, found),
    }
}

fn expression<'a>(
    node: &'a Expr,
    env: &Environment<'a>,
    facts: &Facts<'a>,
    found: &mut Vec<Site<'a>>,
) -> bool {
    if facts.value(node, env).is_none() {
        return false;
    }
    Lookups { env, facts, found }.visit_expr(node);
    true
}

/// The lookup sets of an expression, at any depth.
struct Lookups<'a, 'b> {
    env: &'b Environment<'a>,
    facts: &'b Facts<'a>,
    found: &'b mut Vec<Site<'a>>,
}

impl<'a> Visitor<'a> for Lookups<'a, '_> {
    fn visit_expr(&mut self, node: &'a Expr) {
        if let Expr::Compare(compare) = node {
            for (operator, right) in compare.ops.iter().zip(compare.comparators()) {
                if matches!(operator, CmpOp::In | CmpOp::NotIn)
                    && let Expr::SetComp(comprehension) = right
                    && self.facts.lookup(comprehension, self.env)
                {
                    self.found.push(Site {
                        expression: right,
                        comprehension,
                    });
                }
            }
        }
        visitor::walk_expr(self, node);
    }
}

fn assignment<'a>(statement: &'a Stmt, env: &mut Environment<'a>, facts: &Facts<'a>) -> bool {
    let (target, value): (&'a Expr, &'a Expr) = match statement {
        Stmt::Assign(item) if item.targets.len() == 1 => (&item.targets[0], &item.value),
        Stmt::AnnAssign(item) => match &item.value {
            Some(value) => (&item.target, value),
            None => return false,
        },
        _ => return false,
    };
    let Expr::Name(target) = target else {
        return false;
    };
    match facts.value(value, env) {
        Some(kind) => {
            env.insert(target.id.as_str(), kind);
            true
        }
        None => {
            env.remove(target.id.as_str());
            false
        }
    }
}

fn records<'a>(
    module: &'a ModModule,
    bindings: &Bindings,
) -> HashMap<&'a str, HashMap<&'a str, &'a Expr>> {
    let counts = BindingCounts::new(module, Coverage::Full);
    let mut result = HashMap::new();
    for statement in &module.body {
        let Stmt::ClassDef(owner) = statement else {
            continue;
        };
        let has_arguments = owner
            .arguments
            .as_ref()
            .is_some_and(|items| !items.args.is_empty() || !items.keywords.is_empty());
        if has_arguments
            || owner.type_params.is_some()
            || counts.get(owner.name.as_str()) != 1
            || owner.decorator_list.len() != 1
            || !is_frozen_dataclass(&owner.decorator_list[0].expression, bindings)
        {
            continue;
        }
        let methods: Vec<&str> = owner
            .body
            .iter()
            .filter_map(|node| match node {
                Stmt::FunctionDef(item) => Some(item.name.as_str()),
                _ => None,
            })
            .collect();
        if methods
            .iter()
            .any(|name| matches!(*name, "__getattr__" | "__getattribute__" | "__new__"))
        {
            continue;
        }
        result.insert(owner.name.as_str(), record_fields(owner, &methods));
    }
    result
}

fn is_frozen_dataclass(decorator: &Expr, bindings: &Bindings) -> bool {
    let Expr::Call(call) = decorator else {
        return false;
    };
    let keywords = &call.arguments.keywords;
    qualified_name(&call.func, bindings).as_deref() == Some("dataclasses.dataclass")
        && keywords.iter().all(|keyword| keyword.arg.is_some())
        && keywords.iter().any(|keyword| {
            keyword.arg.as_ref().is_some_and(|arg| arg == "frozen")
                && matches!(&keyword.value, Expr::BooleanLiteral(value) if value.value)
        })
}

fn record_fields<'a>(owner: &'a StmtClassDef, methods: &[&str]) -> HashMap<&'a str, &'a Expr> {
    let statements = owner
        .body
        .iter()
        .filter(|node| !matches!(node, Stmt::FunctionDef(_) | Stmt::ClassDef(_)));
    let plain = statements.clone().all(|node| match node {
        Stmt::AnnAssign(_) | Stmt::Assign(_) => true,
        Stmt::Expr(item) => is_constant(&item.value),
        _ => false,
    });
    if !plain {
        return HashMap::new();
    }
    let counts = BindingCounts::of_statements(statements);
    let mut fields = HashMap::new();
    for statement in &owner.body {
        let Stmt::AnnAssign(field) = statement else {
            continue;
        };
        let Expr::Name(target) = field.target.as_ref() else {
            continue;
        };
        let name = target.id.as_str();
        let stable = field
            .value
            .as_deref()
            .is_none_or(|value| is_constant(value) || value.is_tuple_expr());
        if counts.get(name) == 1 && !methods.contains(&name) && stable {
            fields.insert(name, field.annotation.as_ref());
        }
    }
    fields
}

fn literal_error(node: &Expr, bindings: &Bindings) -> bool {
    let Expr::Call(call) = node else {
        return false;
    };
    qualified_name(&call.func, bindings).is_some_and(|name| ERRORS.contains(&name.as_str()))
        && call.arguments.keywords.is_empty()
        && call.arguments.args.iter().all(is_constant)
}

fn suspends(function: &StmtFunctionDef) -> bool {
    let mut search = Suspension { found: false };
    if let Some(parameters) = &function.type_params {
        search.visit_type_params(parameters);
    }
    search.visit_parameters(&function.parameters);
    if let Some(returns) = &function.returns {
        search.visit_expr(returns);
    }
    search.visit_body(&function.body);
    search.found
}

struct Suspension {
    found: bool,
}

impl<'a> Visitor<'a> for Suspension {
    fn visit_expr(&mut self, node: &'a Expr) {
        if matches!(node, Expr::Await(_) | Expr::Yield(_) | Expr::YieldFrom(_)) {
            self.found = true;
        }
        visitor::walk_expr(self, node);
    }
}

fn writes(statement: &Stmt) -> HashSet<&str> {
    let mut collector = Writes {
        names: HashSet::new(),
    };
    collector.visit_stmt(statement);
    collector.names
}

/// Names stored or deleted, directly or through an attribute or item.
struct Writes<'a> {
    names: HashSet<&'a str>,
}

impl<'a> Visitor<'a> for Writes<'a> {
    fn visit_expr(&mut self, node: &'a Expr) {
        match node {
            Expr::Name(item) if item.ctx != ExprContext::Load => {
                self.names.insert(item.id.as_str());
            }
            Expr::Attribute(item) if item.ctx != ExprContext::Load => {
                self.names.extend(root_name(&item.value));
            }
            Expr::Subscript(item) if item.ctx != ExprContext::Load => {
                self.names.extend(root_name(&item.value));
            }
            _ => {}
        }
        visitor::walk_expr(self, node);
    }
}

fn dependencies(node: &ExprSetComp) -> HashSet<&str> {
    let generator = &node.generators[0];
    let target = match &generator.target {
        Expr::Name(item) => item.id.as_str(),
        _ => "",
    };
    let mut iterated = Reads {
        names: HashSet::new(),
    };
    iterated.visit_expr(&generator.iter);
    let mut projected = Reads {
        names: HashSet::new(),
    };
    projected.visit_expr(&node.elt);
    projected.names.remove(target);
    iterated.names.extend(projected.names);
    iterated.names
}

struct Reads<'a> {
    names: HashSet<&'a str>,
}

impl<'a> Visitor<'a> for Reads<'a> {
    fn visit_expr(&mut self, node: &'a Expr) {
        if let Expr::Name(item) = node {
            self.names.insert(item.id.as_str());
        }
        visitor::walk_expr(self, node);
    }
}

fn lookup_key(source: &str, node: &ExprSetComp) -> String {
    let generator = &node.generators[0];
    let target = match &generator.target {
        Expr::Name(item) => item.id.as_str(),
        _ => "",
    };
    format!(
        "{} -> {}",
        dump_expr(source, &generator.iter),
        dump_expr_renamed(source, &node.elt, target, "<item>")
    )
}
