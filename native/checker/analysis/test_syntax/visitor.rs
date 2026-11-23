//! The flow-sensitive walk of one test module.
use super::binding::{
    Binding, Kind, MONKEYPATCH, annotation_target, fixture_binding, is_mock_constructor,
    is_patch_call,
};
use super::scopes::{expression_bindings, local_names, stored_names};
use super::subprocesses::checked_python_assertions;
use super::{
    AwaitedTask, Fixtures, InstanceAttribute, MockObservation, ObservationKind, SpecView, TestCall,
    TestFunction, TestModule, dotted_module, nonempty_literal,
};
use crate::checker::analysis::namespace::resolve_relative;
use crate::checker::analysis::nodes::is_constant;
use crate::checker::analysis::repr::int_repr;
use crate::checker::analysis::unparse::unparse;
use crate::checker::analysis::walk::{TreeNode, children, expression_key};
use ruff_python_ast::{
    Comprehension, ElifElseClause, ExceptHandler, Expr, ExprAttribute, ExprCall, ExprContext,
    Number, Parameters, Stmt, StmtClassDef, StmtFunctionDef, UnaryOp,
};
use ruff_text_size::Ranged;
use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

type Env<'a> = HashMap<&'a str, Arc<Binding>>;

const MAX_RANGE_ARGS: usize = 3;
const MOCKER_FIXTURES: [&str; 5] = [
    "mocker",
    "class_mocker",
    "module_mocker",
    "package_mocker",
    "session_mocker",
];
const CONSTRUCTORS: [&str; 7] = [
    MONKEYPATCH,
    "asyncio.Event",
    "asyncio.locks.Event",
    "asyncio.Task",
    "asyncio.Future",
    "threading.Event",
    "subprocess.Popen",
];
const MOCK_ASSERTIONS: [&str; 14] = [
    "assert_called",
    "assert_called_once",
    "assert_called_with",
    "assert_called_once_with",
    "assert_any_call",
    "assert_has_calls",
    "assert_not_called",
    "assert_awaited",
    "assert_awaited_once",
    "assert_awaited_with",
    "assert_awaited_once_with",
    "assert_any_await",
    "assert_has_awaits",
    "assert_not_awaited",
];

pub fn is_mock_assertion(name: &str) -> bool {
    MOCK_ASSERTIONS.contains(&name)
}

fn factory_type(qualified: &str) -> Option<&'static str> {
    match qualified {
        "asyncio.get_running_loop" | "asyncio.get_event_loop" => Some("asyncio.AbstractEventLoop"),
        "asyncio.create_task" => Some("asyncio.Task"),
        "asyncio.ensure_future" => Some("asyncio.Future"),
        _ => None,
    }
}

fn async_factory_type(qualified: &str) -> Option<&'static str> {
    matches!(
        qualified,
        "asyncio.create_subprocess_exec" | "asyncio.create_subprocess_shell"
    )
    .then_some("asyncio.subprocess.Process")
}

/// The function that receives recorded evidence.
#[derive(Clone, Copy)]
enum Slot {
    Test(usize),
    Helper(usize),
}

pub struct TestVisitor<'a, 's> {
    pub result: TestModule<'a>,
    module_name: String,
    envs: Vec<Env<'a>>,
    constructors: &'s HashSet<String>,
    fixtures: &'s Fixtures,
    specs: SpecView<'s>,
    helpers: &'s HashMap<usize, &'a StmtFunctionDef>,
    pub defined_fixtures: Fixtures,
    outputs: Option<Vec<Option<Arc<Binding>>>>,
    yielded: bool,
    test: Option<Slot>,
    depth: usize,
    class_name: Option<&'a str>,
    unittest: bool,
    fixtures_only: bool,
}

impl<'a, 's> TestVisitor<'a, 's> {
    pub fn new(
        result: TestModule<'a>,
        (constructors, fixtures): (&'s HashSet<String>, &'s Fixtures),
        specs: SpecView<'s>,
        helpers: &'s HashMap<usize, &'a StmtFunctionDef>,
        fixtures_only: bool,
    ) -> Self {
        let env: Env<'a> = ["all", "range", "tuple"]
            .into_iter()
            .map(|name| (name, Arc::new(Binding::symbol(&format!("builtins.{name}")))))
            .collect();
        Self {
            module_name: dotted_module(result.path),
            result,
            envs: vec![env],
            constructors,
            fixtures,
            specs,
            helpers,
            defined_fixtures: Fixtures::new(),
            outputs: None,
            yielded: false,
            test: None,
            depth: 0,
            class_name: None,
            unittest: false,
            fixtures_only,
        }
    }

    pub fn bind_module_helpers(&mut self) {
        let parsed = self.result.parsed;
        for statement in &parsed.module().body {
            if let Stmt::FunctionDef(node) = statement {
                let line = self.result.function_line(node);
                if self.helpers.contains_key(&line) {
                    let binding = self.helper_binding(node, line);
                    self.env_mut().insert(node.name.as_str(), binding);
                }
            }
        }
    }

    pub fn visit_module(&mut self) {
        let parsed = self.result.parsed;
        for statement in &parsed.module().body {
            self.visit_statement(statement);
        }
    }

    fn env(&self) -> &Env<'a> {
        self.envs.last().expect("the module scope is never popped")
    }

    fn env_mut(&mut self) -> &mut Env<'a> {
        self.envs
            .last_mut()
            .expect("the module scope is never popped")
    }

    fn source(&self) -> &'a str {
        &self.result.parsed.text.text
    }

    fn current(&mut self) -> Option<&mut TestFunction<'a>> {
        match self.test? {
            Slot::Test(index) => self.result.tests.get_mut(index),
            Slot::Helper(line) => self.result.helpers.get_mut(&line),
        }
    }

    fn qualified(&self, node: &Expr) -> Option<String> {
        self.binding(node).map(|binding| binding.qualified.clone())
    }

    fn binding(&self, node: &Expr) -> Option<Arc<Binding>> {
        match node {
            Expr::Name(name) => self.env().get(name.id.as_str()).cloned(),
            Expr::Call(_) | Expr::Await(_) => self.value_binding(node, false),
            Expr::Attribute(attribute) => self.attribute_binding(attribute),
            _ => None,
        }
    }

    fn attribute_binding(&self, node: &ExprAttribute) -> Option<Arc<Binding>> {
        let parent = self.binding(&node.value)?;
        if parent.kind == Kind::Member {
            return None;
        }
        let attr = node.attr.as_str();
        let kind = match parent.kind {
            Kind::Mock => Kind::Mock,
            Kind::Instance => Kind::Member,
            _ => Kind::Symbol,
        };
        let mut binding = Binding::new(
            &format!("{}.{attr}", parent.qualified),
            kind,
            parent.provenance,
        );
        binding.async_callable = parent
            .mock_spec
            .as_deref()
            .filter(|spec| {
                !spec.is_empty()
                    && self
                        .specs
                        .get(spec)
                        .is_some_and(|methods| methods.contains(attr))
            })
            .map(|spec| format!("{spec}.{attr}"));
        binding.mock_origin = parent
            .mock_origin
            .as_deref()
            .filter(|origin| !origin.is_empty())
            .map(|origin| format!("{origin}.{attr}"));
        Some(Arc::new(binding))
    }

    fn value_binding(&self, node: &Expr, context: bool) -> Option<Arc<Binding>> {
        match node {
            Expr::Await(item) => {
                let Expr::Call(call) = item.value.as_ref() else {
                    return None;
                };
                let qualified = self.qualified(&call.func)?;
                let target = async_factory_type(&qualified)?;
                Some(Arc::new(Binding::instance(target, "awaited-factory")))
            }
            Expr::Tuple(tuple)
                if !tuple.elts.is_empty() && !tuple.elts.iter().any(Expr::is_starred_expr) =>
            {
                let mut binding = Binding::nonempty("literal");
                binding.items = Some(
                    tuple
                        .elts
                        .iter()
                        .map(|item| self.value_binding(item, false))
                        .collect(),
                );
                Some(Arc::new(binding))
            }
            Expr::List(_) | Expr::Set(_) | Expr::Dict(_)
                if self.test.is_some() && nonempty_literal(node) =>
            {
                let mut binding = Binding::nonempty("literal");
                binding.mutable = true;
                Some(Arc::new(binding))
            }
            Expr::StringLiteral(_) | Expr::BytesLiteral(_) if nonempty_literal(node) => {
                let mut binding = Binding::nonempty("literal");
                if let Expr::StringLiteral(text) = node {
                    binding.text = Some(text.value.to_str().to_owned());
                }
                Some(Arc::new(binding))
            }
            Expr::Call(call) => self.call_result(call, context),
            other => self.binding(other),
        }
    }

    fn call_result(&self, node: &ExprCall, context: bool) -> Option<Arc<Binding>> {
        let qualified = self.qualified(&node.func)?;
        let name = qualified.as_str();
        if name == "builtins.range" {
            return nonempty_range(node).then(|| Arc::new(Binding::nonempty("literal-range")));
        }
        if self.constructors.contains(name)
            || is_mock_constructor(name)
            || CONSTRUCTORS.contains(&name)
        {
            let mut binding = Binding::instance(name, "constructor");
            if binding.kind == Kind::Mock {
                let spec = node
                    .arguments
                    .keywords
                    .iter()
                    .find(|keyword| {
                        keyword
                            .arg
                            .as_ref()
                            .is_some_and(|arg| matches!(arg.as_str(), "spec" | "spec_set"))
                            && !is_constant(&keyword.value)
                    })
                    .map(|keyword| &keyword.value)
                    .or_else(|| node.arguments.args.first());
                binding.mock_spec = spec.and_then(|spec| self.qualified(spec));
                let (line, column) = self.result.parsed.text.position(usize::from(node.start()));
                binding.mock_origin = Some(format!("{}:{line}:{column}", self.result.path));
            }
            return Some(Arc::new(binding));
        }
        if let Some(target) = factory_type(name) {
            return Some(Arc::new(Binding::instance(target, "known-factory")));
        }
        if name == format!("{MONKEYPATCH}.context") {
            return Some(Arc::new(Binding::instance(MONKEYPATCH, "context")));
        }
        if is_patch_call(name)
            && !name.ends_with(".dict")
            && !name.ends_with(".multiple")
            && !name.starts_with(&format!("{MONKEYPATCH}."))
            && (context || name.starts_with("pytest_mock."))
        {
            return patch_result(node, name);
        }
        None
    }

    fn visit(&mut self, node: TreeNode<'a>) {
        match node {
            TreeNode::Statement(statement) => self.visit_statement(statement),
            TreeNode::Expression(expression) => self.visit_expression(expression),
            TreeNode::Elif(clauses) => {
                if let Some((clause, rest)) = clauses.split_first()
                    && let Some(test) = &clause.test
                {
                    self.visit_if(test, &clause.body, rest);
                }
            }
            TreeNode::Handler(handler) => self.visit_handler(handler),
            other => self.generic(other),
        }
    }

    fn generic(&mut self, node: TreeNode<'a>) {
        for child in children(node) {
            self.visit(child);
        }
    }

    fn visit_body(&mut self, body: &'a [Stmt]) {
        for statement in body {
            self.visit_statement(statement);
        }
    }

    fn visit_statement(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let full = alias.name.as_str();
                    let top = full.split('.').next().unwrap_or(full);
                    let (local, target) = match &alias.asname {
                        Some(name) => (name.as_str(), full),
                        None => (top, top),
                    };
                    self.env_mut()
                        .insert(local, Arc::new(Binding::symbol(target)));
                    self.result.imports.push((statement, full.to_owned()));
                }
            }
            Stmt::ImportFrom(node) => {
                let base = resolve_relative(
                    &self.module_name,
                    false,
                    node.level,
                    node.module.as_ref().map(|name| name.as_str()),
                );
                for alias in &node.names {
                    if alias.name.as_str() == "*" {
                        self.env_mut().clear();
                        continue;
                    }
                    let qualified = if base.is_empty() {
                        alias.name.to_string()
                    } else {
                        format!("{base}.{}", alias.name)
                    };
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    self.env_mut()
                        .insert(local, Arc::new(Binding::symbol(&qualified)));
                    self.result.imports.push((statement, qualified));
                }
            }
            Stmt::ClassDef(node) => self.visit_class(node),
            Stmt::FunctionDef(node) => self.visit_function(statement, node),
            Stmt::Assert(_) => {
                if let Some(function) = self.current() {
                    function.assertions.push(statement);
                }
                self.generic(TreeNode::Statement(statement));
            }
            Stmt::Return(node) => {
                self.generic(TreeNode::Statement(statement));
                if self.outputs.is_some() {
                    let value = node
                        .value
                        .as_deref()
                        .and_then(|value| self.value_binding(value, false));
                    if let Some(outputs) = &mut self.outputs {
                        outputs.push(value);
                    }
                }
            }
            Stmt::Assign(node) => {
                self.visit_expression(&node.value);
                let binding = self.value_binding(&node.value, false);
                for target in &node.targets {
                    self.visit_expression(target);
                    self.forget_mock_spec(target);
                    self.bind_target(target, binding.clone());
                    if matches!(target, Expr::Subscript(_) | Expr::Attribute(_)) {
                        self.forget_mutable();
                        if let Expr::Attribute(attribute) = target
                            && self.qualified(target).as_deref() == Some("sys.executable")
                            && let Expr::Name(owner) = attribute.value.as_ref()
                        {
                            self.env_mut().remove(owner.id.as_str());
                        }
                    }
                }
            }
            Stmt::AnnAssign(node) => {
                self.visit_expression(&node.target);
                if let Some(value) = node.value.as_deref() {
                    self.visit_expression(value);
                    self.forget_mock_spec(&node.target);
                }
                for name in stored_names(&node.target) {
                    self.env_mut().remove(name);
                }
                let annotation = annotation_target(&node.annotation, self.env());
                if node.target.is_name_expr() {
                    let value = node
                        .value
                        .as_deref()
                        .and_then(|value| self.value_binding(value, false));
                    let binding = match value {
                        Some(value)
                            if value.kind == Kind::Nonempty
                                || annotation.as_deref() == Some(value.qualified.as_str()) =>
                        {
                            Some(value)
                        }
                        _ => annotation
                            .as_deref()
                            .filter(|name| !name.is_empty())
                            .map(|name| Arc::new(Binding::instance(name, "annotation"))),
                    };
                    self.bind_target(&node.target, binding);
                } else if node.value.is_some() {
                    self.forget_mutable();
                }
            }
            Stmt::AugAssign(node) => {
                self.visit_expression(&node.value);
                self.forget_mutable();
                for name in stored_names(&node.target) {
                    self.env_mut().remove(name);
                }
            }
            Stmt::Delete(node) => {
                self.forget_mutable();
                for target in &node.targets {
                    self.forget_mock_spec(target);
                    for name in stored_names(target) {
                        self.env_mut().remove(name);
                    }
                }
            }
            Stmt::If(node) => self.visit_if(&node.test, &node.body, &node.elif_else_clauses),
            Stmt::For(node) => {
                self.visit_expression(&node.iter);
                for name in stored_names(&node.target) {
                    self.env_mut().remove(name);
                }
                self.branches(&[statements(&node.body), statements(&node.orelse)]);
            }
            Stmt::While(node) => {
                self.visit_expression(&node.test);
                self.branches(&[statements(&node.body), statements(&node.orelse)]);
            }
            Stmt::With(node) => {
                for item in &node.items {
                    self.visit_expression(&item.context_expr);
                    let binding = self.value_binding(&item.context_expr, true);
                    if let Some(variables) = item.optional_vars.as_deref() {
                        for name in stored_names(variables) {
                            self.env_mut().remove(name);
                        }
                        if let (Expr::Name(name), Some(binding)) = (variables, binding) {
                            self.env_mut().insert(name.id.as_str(), binding);
                        }
                    }
                }
                self.visit_body(&node.body);
            }
            Stmt::Try(node) => {
                let mut bodies = vec![statements(&node.body)];
                bodies.extend(
                    node.handlers
                        .iter()
                        .map(|handler| vec![TreeNode::Handler(handler)]),
                );
                bodies.push(statements(&node.orelse));
                self.branches(&bodies);
                self.visit_body(&node.finalbody);
            }
            Stmt::Match(node) => {
                self.visit_expression(&node.subject);
                for case in &node.cases {
                    let copy = self.env().clone();
                    self.envs.push(copy);
                    for name in expression_bindings(TreeNode::Pattern(&case.pattern)) {
                        self.env_mut().remove(name);
                    }
                    if let Some(guard) = case.guard.as_deref() {
                        self.visit_expression(guard);
                    }
                    self.visit_body(&case.body);
                    self.envs.pop();
                }
                self.forget_mutable();
                for name in local_names(&[TreeNode::Statement(statement)]) {
                    self.env_mut().remove(name);
                }
            }
            _ => self.generic(TreeNode::Statement(statement)),
        }
    }

    fn visit_if(&mut self, test: &'a Expr, body: &'a [Stmt], clauses: &'a [ElifElseClause]) {
        self.visit_expression(test);
        let orelse = match clauses.first() {
            Some(clause) if clause.test.is_some() => vec![TreeNode::Elif(clauses)],
            Some(clause) => statements(&clause.body),
            None => Vec::new(),
        };
        self.branches(&[statements(body), orelse]);
    }

    fn visit_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        if let Some(kind) = node.type_.as_deref() {
            self.visit_expression(kind);
        }
        if let Some(name) = &node.name {
            self.env_mut().remove(name.as_str());
        }
        self.visit_body(&node.body);
    }

    fn branches(&mut self, bodies: &[Vec<TreeNode<'a>>]) {
        for body in bodies {
            let copy = self.env().clone();
            self.envs.push(copy);
            for node in body {
                self.visit(*node);
            }
            self.envs.pop();
        }
        self.forget_mutable();
        let all: Vec<TreeNode<'a>> = bodies.iter().flatten().copied().collect();
        for name in local_names(&all) {
            self.env_mut().remove(name);
        }
    }

    fn forget_mutable(&mut self) {
        self.env_mut().retain(|_, binding| !binding.mutable);
    }

    fn forget_mock_spec(&mut self, target: &Expr) {
        let Expr::Attribute(attribute) = target else {
            return;
        };
        let Some(receiver) = self.binding(&attribute.value) else {
            return;
        };
        let has_spec = receiver
            .mock_spec
            .as_deref()
            .is_some_and(|spec| !spec.is_empty());
        let has_origin = receiver
            .mock_origin
            .as_deref()
            .is_some_and(|origin| !origin.is_empty());
        if !has_spec || !has_origin {
            return;
        }
        for binding in self.env_mut().values_mut() {
            if binding.mock_origin == receiver.mock_origin {
                let mut updated = binding.as_ref().clone();
                updated.mock_spec = None;
                *binding = Arc::new(updated);
            }
        }
    }

    fn bind_target(&mut self, target: &'a Expr, binding: Option<Arc<Binding>>) {
        for name in stored_names(target) {
            self.env_mut().remove(name);
        }
        let Some(binding) = binding else {
            return;
        };
        match target {
            Expr::Name(name) => {
                let value = if binding.kind == Kind::Mock && binding.mock_origin.is_none() {
                    let (line, _) = self
                        .result
                        .parsed
                        .text
                        .position(usize::from(target.start()));
                    let mut updated = binding.as_ref().clone();
                    updated.mock_origin = Some(format!("{}:{line}:{}", self.result.path, name.id));
                    Arc::new(updated)
                } else {
                    binding
                };
                self.env_mut().insert(name.id.as_str(), value);
            }
            Expr::Tuple(_) | Expr::List(_) => {
                let elements = match target {
                    Expr::Tuple(item) => &item.elts,
                    Expr::List(item) => &item.elts,
                    _ => return,
                };
                if let Some(items) = &binding.items
                    && items.len() == elements.len()
                {
                    for (element, value) in elements.iter().zip(items) {
                        self.bind_target(element, value.clone());
                    }
                }
            }
            _ => {}
        }
    }

    fn visit_class(&mut self, node: &'a StmtClassDef) {
        let (class_name, unittest) = (self.class_name, self.unittest);
        for decorator in &node.decorator_list {
            self.visit_expression(&decorator.expression);
        }
        for base in node.bases() {
            self.visit_expression(base);
        }
        for keyword in node.keywords() {
            self.visit_expression(&keyword.value);
        }
        self.unittest = node.bases().iter().any(|base| {
            matches!(
                self.qualified(base).as_deref(),
                Some("unittest.TestCase" | "unittest.IsolatedAsyncioTestCase")
            )
        });
        self.class_name = Some(node.name.as_str());
        let copy = self.env().clone();
        self.envs.push(copy);
        self.visit_body(&node.body);
        self.envs.pop();
        self.class_name = class_name;
        self.unittest = unittest;
        self.env_mut().remove(node.name.as_str());
        let qualified = format!("{}.{}", self.module_name, node.name);
        if self.specs.contains(&qualified) {
            self.env_mut()
                .insert(node.name.as_str(), Arc::new(Binding::symbol(&qualified)));
        }
    }

    fn visit_function(&mut self, statement: &'a Stmt, node: &'a StmtFunctionDef) {
        let outer_test = self.test;
        let outer_yielded = self.yielded;
        let line = self.result.function_line(node);
        let fixture = node.decorator_list.iter().any(|decorator| {
            let target = match &decorator.expression {
                Expr::Call(call) => call.func.as_ref(),
                other => other,
            };
            matches!(
                self.qualified(target).as_deref(),
                Some("pytest.fixture" | "pytest_asyncio.fixture")
            )
        });
        let collected = self.depth == 0
            && node.name.starts_with("test_")
            && self
                .class_name
                .is_none_or(|name| name.starts_with("Test") || self.unittest);
        let function = self.function_context(statement, node, line, collected);
        // Nested defaults and decorators run while defining the helper, before any
        // later call-site deadline. Only its body belongs to its summary.
        self.test = if collected { function } else { outer_test };
        for decorator in &node.decorator_list {
            self.visit_expression(&decorator.expression);
        }
        self.visit_defaults(&node.parameters);
        if self.fixtures_only && !fixture {
            self.leave_function(node, line, outer_test);
            return;
        }
        self.test = function;
        let scope = if self.class_name.is_some() && self.depth == 0 {
            self.envs[0].clone()
        } else {
            self.env().clone()
        };
        self.envs.push(scope);
        for name in local_names(&statements(&node.body)) {
            self.env_mut().remove(name);
        }
        self.bind_parameters(node, line, collected, fixture);
        let outer_outputs = std::mem::replace(&mut self.outputs, fixture.then(Vec::new));
        self.yielded = false;
        self.depth += 1;
        self.visit_body(&node.body);
        self.depth -= 1;
        if fixture {
            self.record_fixture(node);
        }
        self.envs.pop();
        self.outputs = outer_outputs;
        self.yielded = outer_yielded;
        self.leave_function(node, line, outer_test);
    }

    fn visit_defaults(&mut self, parameters: &'a Parameters) {
        for item in parameters.posonlyargs.iter().chain(&parameters.args) {
            if let Some(default) = item.default.as_deref() {
                self.visit_expression(default);
            }
        }
        for item in &parameters.kwonlyargs {
            if let Some(default) = item.default.as_deref() {
                self.visit_expression(default);
            }
        }
    }

    fn bind_parameters(
        &mut self,
        node: &'a StmtFunctionDef,
        line: usize,
        collected: bool,
        fixture: bool,
    ) {
        let parameters = &node.parameters;
        let arguments = parameters
            .posonlyargs
            .iter()
            .chain(&parameters.args)
            .chain(&parameters.kwonlyargs)
            .map(|item| &item.parameter)
            .chain(parameters.vararg.as_deref())
            .chain(parameters.kwarg.as_deref());
        let top_level = self.depth == 0;
        let injected = top_level && (collected || fixture);
        for argument in arguments {
            let name = argument.name.as_str();
            self.env_mut().remove(name);
            let class_key = format!("{}.{name}", self.class_name.unwrap_or("None"));
            let key = if self.fixtures.contains_key(&class_key) {
                class_key.as_str()
            } else {
                name
            };
            let has_fixture = self.fixtures.contains_key(key);
            let written = argument.annotation();
            let annotation = written.and_then(|item| annotation_target(item, self.env()));
            if injected && let Some(binding) = self.fixtures.get(key).cloned() {
                self.env_mut().insert(name, binding);
            }
            if let Some(target) = annotation.as_deref().filter(|item| !item.is_empty()) {
                let existing = self
                    .env()
                    .get(name)
                    .filter(|item| item.qualified == target)
                    .cloned();
                let value =
                    existing.unwrap_or_else(|| Arc::new(Binding::instance(target, "annotation")));
                self.env_mut().insert(name, value);
            }
            if let Some(binding) = self.env().get(name).cloned()
                && binding.kind == Kind::Mock
                && binding.mock_origin.is_none()
            {
                let mut updated = binding.as_ref().clone();
                updated.mock_origin = Some(format!("{}:{line}:{name}", self.result.path));
                self.env_mut().insert(name, Arc::new(updated));
            }
            let implicit = injected && !has_fixture && annotation.is_none();
            if top_level
                && ((MOCKER_FIXTURES.contains(&name) && implicit)
                    || written.is_some_and(|item| {
                        self.qualified(item).as_deref() == Some("pytest_mock.MockerFixture")
                    }))
            {
                let binding = Binding::new("pytest_mock.MockerFixture", Kind::Symbol, "fixture");
                self.env_mut().insert(name, Arc::new(binding));
            }
            if name == "monkeypatch" && implicit {
                let binding = Binding::instance(MONKEYPATCH, "fixture");
                self.env_mut().insert(name, Arc::new(binding));
            }
        }
        if self.unittest
            && top_level
            && let Some(first) = parameters.args.first()
        {
            let binding = Binding::instance("unittest.TestCase", "unittest-self");
            self.env_mut()
                .insert(first.parameter.name.as_str(), Arc::new(binding));
        }
    }

    fn record_fixture(&mut self, node: &'a StmtFunctionDef) {
        if self.depth != 0 {
            return;
        }
        let outer = &self.envs[self.envs.len() - 2];
        let mut binding = node
            .returns
            .as_deref()
            .and_then(|returns| fixture_binding(returns, outer));
        if let Some(outputs) = &self.outputs
            && let Some(Some(first)) = outputs.first()
            && outputs
                .iter()
                .all(|output| output.as_ref().is_some_and(|item| item == first))
            && (self.yielded || matches!(node.body.last(), Some(Stmt::Return(_))))
        {
            let inferred = first.inferred_fixture();
            if node.returns.is_none() {
                binding = Some(inferred);
            } else if binding
                .as_ref()
                .is_some_and(|item| item.kind == Kind::Mock && item.qualified == inferred.qualified)
            {
                let mut updated = inferred;
                updated.provenance = "fixture-return";
                binding = Some(updated);
            }
        }
        let mut fixture_name = node.name.as_str();
        for decorator in &node.decorator_list {
            if let Expr::Call(call) = &decorator.expression {
                for keyword in &call.arguments.keywords {
                    if keyword
                        .arg
                        .as_ref()
                        .is_some_and(|arg| arg.as_str() == "name")
                        && let Expr::StringLiteral(text) = &keyword.value
                    {
                        fixture_name = text.value.to_str();
                    }
                }
            }
        }
        let key = match self.class_name {
            Some(class_name) => format!("{class_name}.{fixture_name}"),
            None => fixture_name.to_owned(),
        };
        let value = binding.unwrap_or_else(|| Binding::new("", Kind::Symbol, "unknown-fixture"));
        self.defined_fixtures.insert(key, Arc::new(value));
    }

    fn helper_binding(&self, node: &StmtFunctionDef, line: usize) -> Arc<Binding> {
        let mut binding = Binding::new(
            &format!("{}.{}", self.module_name, node.name),
            Kind::Helper,
            "local-helper",
        );
        binding.helper_line = Some(line);
        Arc::new(binding)
    }

    fn leave_function(&mut self, node: &'a StmtFunctionDef, line: usize, test: Option<Slot>) {
        self.test = test;
        self.env_mut().remove(node.name.as_str());
        if self.helpers.contains_key(&line) {
            let binding = self.helper_binding(node, line);
            self.env_mut().insert(node.name.as_str(), binding);
        }
    }

    fn function_context(
        &mut self,
        statement: &'a Stmt,
        node: &'a StmtFunctionDef,
        line: usize,
        collected: bool,
    ) -> Option<Slot> {
        if !collected && !self.helpers.contains_key(&line) {
            return None;
        }
        let name = match self.class_name {
            Some(class_name) => format!("{class_name}.{}", node.name),
            None => node.name.to_string(),
        };
        let function = TestFunction::new(statement, node, name);
        if collected {
            self.result.tests.push(function);
            Some(Slot::Test(self.result.tests.len() - 1))
        } else {
            self.result.helpers.insert(line, function);
            Some(Slot::Helper(line))
        }
    }

    fn visit_expression(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Call(node) => self.visit_call(expression, node),
            Expr::Name(node) => {
                let nonempty = node.ctx == ExprContext::Load
                    && self
                        .env()
                        .get(node.id.as_str())
                        .is_some_and(|binding| binding.kind == Kind::Nonempty);
                if nonempty && let Some(function) = self.current() {
                    function
                        .nonempty_iterables
                        .insert(expression_key(expression));
                }
            }
            Expr::Attribute(node) => self.visit_attribute(expression, node),
            Expr::Await(node) => {
                let binding = self.binding(&node.value);
                if let Some(binding) = binding
                    && binding.kind == Kind::Instance
                    && matches!(
                        binding.qualified.as_str(),
                        "asyncio.Task" | "asyncio.Future"
                    )
                    && let Some(function) = self.current()
                {
                    function.awaited_tasks.push(AwaitedTask {
                        node: expression,
                        qualified: binding.qualified.clone(),
                        provenance: binding.provenance,
                    });
                }
                self.generic(TreeNode::Expression(expression));
            }
            Expr::Yield(node) => {
                self.generic(TreeNode::Expression(expression));
                if self.outputs.is_some() {
                    self.yielded = true;
                    let value = node
                        .value
                        .as_deref()
                        .and_then(|value| self.value_binding(value, false));
                    if let Some(outputs) = &mut self.outputs {
                        outputs.push(value);
                    }
                }
            }
            Expr::YieldFrom(_) => {
                self.generic(TreeNode::Expression(expression));
                if let Some(outputs) = &mut self.outputs {
                    outputs.push(None);
                }
            }
            Expr::Named(node) => {
                self.visit_expression(&node.value);
                for name in stored_names(&node.target) {
                    self.env_mut().remove(name);
                }
            }
            Expr::Lambda(node) => {
                let test = self.test;
                if let Some(parameters) = node.parameters.as_deref() {
                    self.visit_defaults(parameters);
                }
                let copy = self.env().clone();
                self.envs.push(copy);
                self.test = None;
                if let Some(parameters) = node.parameters.as_deref() {
                    let names = parameters
                        .posonlyargs
                        .iter()
                        .chain(&parameters.args)
                        .chain(&parameters.kwonlyargs)
                        .map(|item| &item.parameter)
                        .chain(parameters.vararg.as_deref())
                        .chain(parameters.kwarg.as_deref());
                    for parameter in names {
                        self.env_mut().remove(parameter.name.as_str());
                    }
                }
                self.visit_expression(&node.body);
                self.envs.pop();
                self.test = test;
            }
            Expr::ListComp(node) => {
                self.visit_comprehension(&node.generators, &[Some(&node.elt)]);
            }
            Expr::SetComp(node) => {
                self.visit_comprehension(&node.generators, &[Some(&node.elt)]);
            }
            Expr::DictComp(node) => {
                self.visit_comprehension(
                    &node.generators,
                    &[node.key.as_deref(), Some(&node.value)],
                );
            }
            Expr::Generator(node) => {
                let test = self.test;
                // Only the outer iterable is evaluated in this scope at construction.
                if let Some(first) = node.generators.first() {
                    let nonempty = self
                        .value_binding(&first.iter, false)
                        .is_some_and(|binding| binding.kind == Kind::Nonempty);
                    if nonempty && let Some(function) = self.current() {
                        function
                            .nonempty_iterables
                            .insert(expression_key(&first.iter));
                    }
                }
                self.test = None;
                self.visit_comprehension(&node.generators, &[Some(&node.elt)]);
                self.test = test;
            }
            _ => self.generic(TreeNode::Expression(expression)),
        }
    }

    fn visit_comprehension(
        &mut self,
        generators: &'a [Comprehension],
        values: &[Option<&'a Expr>],
    ) {
        let copy = self.env().clone();
        self.envs.push(copy);
        for generator in generators {
            self.visit_expression(&generator.iter);
            for name in stored_names(&generator.target) {
                self.env_mut().remove(name);
            }
            for condition in &generator.ifs {
                self.visit_expression(condition);
            }
        }
        for value in values.iter().flatten() {
            self.visit_expression(value);
        }
        self.envs.pop();
        self.forget_mutable();
    }

    fn visit_call(&mut self, expression: &'a Expr, node: &'a ExprCall) {
        if let Some(binding) = self.binding(&node.func) {
            let call = Arc::new(TestCall {
                node: expression,
                qualified: binding.qualified.clone(),
                provenance: binding.provenance,
                mock_receiver: binding.kind == Kind::Mock,
                helper_line: binding.helper_line,
            });
            self.result.calls.push(Arc::clone(&call));
            if self.test.is_some() {
                let checked = checked_python_assertions(
                    node,
                    &binding.qualified,
                    &|item| self.qualified(item),
                    &|item| self.literal_text(item),
                );
                let nonempty = self
                    .value_binding(expression, false)
                    .is_some_and(|result| result.kind == Kind::Nonempty);
                let replaces_clock = binding.qualified == format!("{MONKEYPATCH}.setattr")
                    && node.arguments.args.len() > 1
                    && self.qualified(&node.arguments.args[0]).as_deref()
                        == Some("asyncio.AbstractEventLoop")
                    && matches!(&node.arguments.args[1],
                        Expr::StringLiteral(text) if text.value.to_str() == "time");
                if let Some(function) = self.current() {
                    function.calls.push(call);
                    if checked {
                        function.child_assertions.push(expression);
                    }
                    if nonempty {
                        function
                            .nonempty_iterables
                            .insert(expression_key(expression));
                    }
                    if replaces_clock {
                        function.clock_replacements.push(expression);
                    }
                }
            }
        }
        if let Expr::Attribute(attribute) = node.func.as_ref() {
            let name = attribute.attr.as_str();
            if is_mock_assertion(name) && name != "assert_not_called" {
                let kind = if name.contains("await") {
                    ObservationKind::Await
                } else {
                    ObservationKind::Call
                };
                self.record_observation(expression, &attribute.value, kind);
            }
        }
        self.generic(TreeNode::Expression(expression));
        // Unknown calls can mutate an escaped local collection, including through a
        // closure or alias. Earlier evidence stays valid; later reads need their own.
        self.forget_mutable();
    }

    fn record_observation(&mut self, node: &'a Expr, receiver: &'a Expr, kind: ObservationKind) {
        if self.test.is_none() {
            return;
        }
        let Some(binding) = self.binding(receiver) else {
            return;
        };
        let (Some(async_callable), Some(origin)) = (
            binding
                .async_callable
                .as_deref()
                .filter(|item| !item.is_empty()),
            binding
                .mock_origin
                .as_deref()
                .filter(|item| !item.is_empty()),
        ) else {
            return;
        };
        let observation = MockObservation {
            node,
            origin: origin.to_owned(),
            receiver: unparse(self.source(), receiver),
            kind,
            async_callable: async_callable.to_owned(),
            provenance: binding.provenance,
        };
        if let Some(function) = self.current() {
            function.mock_observations.push(observation);
        }
    }

    fn visit_attribute(&mut self, expression: &'a Expr, node: &'a ExprAttribute) {
        let qualified = self
            .attribute_binding(node)
            .map(|item| item.qualified.clone());
        let receiver = self.binding(&node.value);
        if qualified.as_deref() == Some("asyncio.AbstractEventLoop.time")
            && node.ctx == ExprContext::Store
            && let Some(function) = self.current()
        {
            function.clock_replacements.push(expression);
        }
        if let Some(name) = qualified.filter(|name| !name.is_empty()) {
            match &receiver {
                Some(owner) if owner.kind == Kind::Instance => {
                    if node.ctx == ExprContext::Load {
                        self.result.instance_attributes.push(InstanceAttribute {
                            node: expression,
                            qualified: name,
                            provenance: owner.provenance,
                        });
                    }
                }
                _ => self.result.attributes.push((expression, name)),
            }
        }
        if node.ctx == ExprContext::Load {
            match node.attr.as_str() {
                "called" | "call_count" | "call_args" | "call_args_list" => {
                    self.record_observation(expression, &node.value, ObservationKind::Call);
                }
                "await_count" | "await_args" | "await_args_list" => {
                    self.record_observation(expression, &node.value, ObservationKind::Await);
                }
                _ => {}
            }
        }
        self.generic(TreeNode::Expression(expression));
    }

    fn literal_text(&self, node: &Expr) -> Option<String> {
        match node {
            Expr::StringLiteral(text) => Some(text.value.to_str().to_owned()),
            Expr::Name(name) => self
                .env()
                .get(name.id.as_str())
                .and_then(|binding| binding.text.clone()),
            _ => None,
        }
    }
}

fn statements(body: &[Stmt]) -> Vec<TreeNode<'_>> {
    body.iter().map(TreeNode::Statement).collect()
}

fn patch_result(node: &ExprCall, qualified: &str) -> Option<Arc<Binding>> {
    let new_position = if qualified.ends_with(".object") { 2 } else { 1 };
    if node.arguments.args.len() > new_position
        || node.arguments.keywords.iter().any(|keyword| {
            keyword
                .arg
                .as_ref()
                .is_none_or(|arg| matches!(arg.as_str(), "new" | "new_callable"))
        })
    {
        return None;
    }
    Some(Arc::new(Binding::instance(
        "unittest.mock.MagicMock",
        "patch-result",
    )))
}

/// A signed integer of any size, as a sign and its decimal digits.
#[derive(Clone, PartialEq, Eq)]
struct Whole {
    negative: bool,
    digits: String,
}

impl Whole {
    fn zero() -> Self {
        Self {
            negative: false,
            digits: "0".to_owned(),
        }
    }

    fn is_zero(&self) -> bool {
        self.digits == "0"
    }

    fn compare(&self, other: &Self) -> Ordering {
        let magnitude = self
            .digits
            .len()
            .cmp(&other.digits.len())
            .then_with(|| self.digits.cmp(&other.digits));
        match (self.negative, other.negative) {
            (false, false) => magnitude,
            (true, true) => magnitude.reverse(),
            (true, false) => Ordering::Less,
            (false, true) => Ordering::Greater,
        }
    }
}

fn literal_whole(node: &Expr) -> Option<Whole> {
    let (negative, value) = match node {
        Expr::UnaryOp(item) if item.op == UnaryOp::USub => (true, item.operand.as_ref()),
        other => (false, other),
    };
    let Expr::NumberLiteral(number) = value else {
        return None;
    };
    let Number::Int(int) = &number.value else {
        return None;
    };
    let text = int_repr(int).replace('_', "");
    let trimmed = text.trim_start_matches('0');
    let digits = if trimmed.is_empty() { "0" } else { trimmed };
    Some(Whole {
        negative: negative && digits != "0",
        digits: digits.to_owned(),
    })
}

fn nonempty_range(node: &ExprCall) -> bool {
    let arguments = &node.arguments;
    if !arguments.keywords.is_empty()
        || arguments.args.is_empty()
        || arguments.args.len() > MAX_RANGE_ARGS
    {
        return false;
    }
    let Some(values) = arguments
        .args
        .iter()
        .map(literal_whole)
        .collect::<Option<Vec<_>>>()
    else {
        return false;
    };
    let (start, stop, step) = match values.as_slice() {
        [stop] => (Whole::zero(), stop.clone(), None),
        [start, stop] => (start.clone(), stop.clone(), None),
        [start, stop, step] => (start.clone(), stop.clone(), Some(step.clone())),
        _ => return false,
    };
    match step {
        Some(step) if step.is_zero() => false,
        Some(step) if step.negative => start.compare(&stop) == Ordering::Greater,
        _ => start.compare(&stop) == Ordering::Less,
    }
}
