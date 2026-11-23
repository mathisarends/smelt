//! Scoped syntactic test evidence; analyzed tests are never imported or executed.
mod awaits;
mod binding;
mod cases;
mod deadlines;
mod helpers;
mod outcomes;
mod scopes;
mod subprocesses;
mod timing;
mod visitor;

pub use awaits::call_only_async_mocks;
pub use cases::independent_case_loops;
pub use outcomes::{helper_outcome_review, vacuous_outcomes};
pub use timing::unbounded_waits;

use super::namespace::{Namespace, namespace_of, resolve_dotted};
use super::unparse::unparse;
use super::walk::{NodeKey, TreeNode, children, expression_key, nodes_below};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use binding::{Binding, MONKEYPATCH, is_mock_call, is_patch_call};
use helpers::{helper_executes, local_helpers};
use rayon::prelude::*;
use ruff_python_ast::{Expr, ExprCall, Stmt, StmtFunctionDef};
use ruff_text_size::TextSize;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::Arc;
use visitor::{TestVisitor, is_mock_assertion};

type Fixtures = HashMap<String, Arc<Binding>>;
type AsyncSpecs = HashMap<String, BTreeSet<String>>;

/// The project's async method specs with those of the analyzed file on top.
#[derive(Clone, Copy)]
struct SpecView<'s> {
    shared: &'s AsyncSpecs,
    own: Option<&'s AsyncSpecs>,
}

impl<'s> SpecView<'s> {
    fn get(&self, name: &str) -> Option<&'s BTreeSet<String>> {
        self.own
            .and_then(|own| own.get(name))
            .or_else(|| self.shared.get(name))
    }

    fn contains(&self, name: &str) -> bool {
        self.get(name).is_some()
    }
}

pub struct TestCall<'a> {
    pub node: &'a Expr,
    pub qualified: String,
    pub provenance: &'static str,
    pub mock_receiver: bool,
    pub helper_line: Option<usize>,
}

impl TestCall<'_> {
    pub fn is_patch(&self) -> bool {
        is_patch_call(&self.qualified)
    }

    pub fn is_mock(&self) -> bool {
        self.is_patch() || is_mock_call(&self.qualified)
    }

    pub fn family(&self) -> &'static str {
        if self.qualified.starts_with(&format!("{MONKEYPATCH}.")) {
            "pytest.MonkeyPatch"
        } else if self.qualified.starts_with("pytest_mock.") {
            "pytest-mock"
        } else {
            "unittest.mock"
        }
    }

    fn interaction_assertion(&self) -> bool {
        self.mock_receiver
            && is_mock_assertion(self.qualified.rsplit('.').next().unwrap_or_default())
    }

    pub fn has_string_target(&self) -> bool {
        if !self.is_patch() || self.qualified.ends_with(".object") {
            return false;
        }
        let Expr::Call(call) = self.node else {
            return false;
        };
        if self.qualified.starts_with(&format!("{MONKEYPATCH}.")) {
            // setattr("pkg.name", replacement) vs setattr(obj, "name", replacement).
            let positional = if self.qualified.ends_with(".setattr") {
                2
            } else {
                1
            };
            if call.arguments.args.len() > positional
                || call
                    .arguments
                    .keywords
                    .iter()
                    .any(|keyword| keyword.arg.as_ref().is_some_and(|arg| arg == "name"))
            {
                return false;
            }
        }
        matches!(patch_target(call), Some(Expr::StringLiteral(_)))
    }
}

fn patch_target(call: &ExprCall) -> Option<&Expr> {
    call.arguments.args.first().or_else(|| {
        call.arguments
            .keywords
            .iter()
            .find(|keyword| {
                keyword
                    .arg
                    .as_ref()
                    .is_some_and(|arg| matches!(arg.as_str(), "target" | "in_dict"))
            })
            .map(|keyword| &keyword.value)
    })
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ObservationKind {
    Call,
    Await,
}

pub struct MockObservation<'a> {
    pub node: &'a Expr,
    pub origin: String,
    pub receiver: String,
    pub kind: ObservationKind,
    pub async_callable: String,
    pub provenance: &'static str,
}

pub struct HelperCall<'a> {
    pub node: &'a Expr,
    pub line: usize,
}

pub struct AwaitedTask<'a> {
    pub node: &'a Expr,
    pub qualified: String,
    pub provenance: &'static str,
}

pub struct InstanceAttribute<'a> {
    pub node: &'a Expr,
    pub qualified: String,
    pub provenance: &'static str,
}

pub struct TestFunction<'a> {
    pub statement: &'a Stmt,
    pub node: &'a StmtFunctionDef,
    pub name: String,
    pub calls: Vec<Arc<TestCall<'a>>>,
    pub assertions: Vec<&'a Stmt>,
    pub clock_replacements: Vec<&'a Expr>,
    pub awaited_tasks: Vec<AwaitedTask<'a>>,
    pub nonempty_iterables: HashSet<NodeKey>,
    pub mock_observations: Vec<MockObservation<'a>>,
    pub child_assertions: Vec<&'a Expr>,
    pub helpers: Vec<HelperCall<'a>>,
    parents: HashMap<NodeKey, TreeNode<'a>>,
    call_names: HashMap<NodeKey, usize>,
}

impl<'a> TestFunction<'a> {
    fn new(statement: &'a Stmt, node: &'a StmtFunctionDef, name: String) -> Self {
        Self {
            statement,
            node,
            name,
            calls: Vec::new(),
            assertions: Vec::new(),
            clock_replacements: Vec::new(),
            awaited_tasks: Vec::new(),
            nonempty_iterables: HashSet::new(),
            mock_observations: Vec::new(),
            child_assertions: Vec::new(),
            helpers: Vec::new(),
            parents: HashMap::new(),
            call_names: HashMap::new(),
        }
    }

    fn index(&mut self) {
        for parent in nodes_below(TreeNode::Statement(self.statement)) {
            for child in children(parent) {
                self.parents.insert(child.key(), parent);
            }
        }
        for (index, call) in self.calls.iter().enumerate() {
            self.call_names.insert(expression_key(call.node), index);
        }
    }

    fn parent(&self, node: TreeNode<'a>) -> Option<TreeNode<'a>> {
        self.parents.get(&node.key()).copied()
    }

    fn call_name(&self, node: &Expr) -> Option<&str> {
        self.call_names
            .get(&expression_key(node))
            .map(|index| self.calls[*index].qualified.as_str())
    }
}

pub struct TestModule<'a> {
    pub path: &'a str,
    pub parsed: &'a ParsedSource,
    pub imports: Vec<(&'a Stmt, String)>,
    pub attributes: Vec<(&'a Expr, String)>,
    pub calls: Vec<Arc<TestCall<'a>>>,
    pub tests: Vec<TestFunction<'a>>,
    pub helpers: HashMap<usize, TestFunction<'a>>,
    pub instance_attributes: Vec<InstanceAttribute<'a>>,
}

impl<'a> TestModule<'a> {
    fn new(path: &'a str, parsed: &'a ParsedSource) -> Self {
        Self {
            path,
            parsed,
            imports: Vec::new(),
            attributes: Vec::new(),
            calls: Vec::new(),
            tests: Vec::new(),
            helpers: HashMap::new(),
            instance_attributes: Vec::new(),
        }
    }

    /// The 1-based line of an offset.
    pub fn line(&self, offset: TextSize) -> usize {
        self.parsed.text.position(usize::from(offset)).0
    }

    /// The 1-based line and 1-based column of an offset.
    pub fn place(&self, offset: TextSize) -> (usize, usize) {
        let (line, column) = self.parsed.text.position(usize::from(offset));
        (line, column + 1)
    }

    /// The line of `def`, after any decorators.
    pub fn function_line(&self, node: &StmtFunctionDef) -> usize {
        self.line(self.parsed.function_start(node))
    }

    fn resolve_helpers(&mut self) {
        for function in self.helpers.values_mut() {
            function.index();
        }
        for test in &mut self.tests {
            test.index();
            let mut found = Vec::new();
            for call in &test.calls {
                if let Some(line) = call.helper_line
                    && let Some(helper) = self.helpers.get(&line)
                    && helper_executes(helper.node, call.node, test)
                {
                    found.push(HelperCall {
                        node: call.node,
                        line,
                    });
                }
            }
            test.helpers = found;
        }
    }
}

/// `str.removesuffix(".py").replace("/", ".")`.
fn dotted_module(path: &str) -> String {
    path.strip_suffix(".py").unwrap_or(path).replace('/', ".")
}

/// `nonempty_literal`: a literal that certainly holds at least one item.
fn nonempty_literal(node: &Expr) -> bool {
    match node {
        Expr::List(item) => has_plain_items(&item.elts),
        Expr::Tuple(item) => has_plain_items(&item.elts),
        Expr::Set(item) => has_plain_items(&item.elts),
        Expr::Dict(item) => {
            !item.items.is_empty() && item.items.iter().all(|entry| entry.key.is_some())
        }
        Expr::StringLiteral(item) => !item.value.to_str().is_empty(),
        Expr::BytesLiteral(item) => item.value.bytes().next().is_some(),
        _ => false,
    }
}

fn has_plain_items(items: &[Expr]) -> bool {
    !items.is_empty() && !items.iter().any(Expr::is_starred_expr)
}

fn class_async_methods(parsed: &ParsedSource, module: &str) -> AsyncSpecs {
    let tree = parsed.module();
    let mut decorators: HashSet<String> =
        HashSet::from(["staticmethod".to_owned(), "classmethod".to_owned()]);
    for statement in &tree.body {
        match statement {
            Stmt::ImportFrom(node)
                if node.level == 0
                    && node
                        .module
                        .as_ref()
                        .is_some_and(|name| name.as_str() == "abc") =>
            {
                decorators.extend(
                    node.names
                        .iter()
                        .filter(|alias| alias.name.as_str() == "abstractmethod")
                        .map(|alias| alias.asname.as_ref().unwrap_or(&alias.name).to_string()),
                );
            }
            Stmt::Import(node) => {
                decorators.extend(
                    node.names
                        .iter()
                        .filter(|alias| alias.name.as_str() == "abc")
                        .map(|alias| {
                            format!(
                                "{}.abstractmethod",
                                alias.asname.as_ref().unwrap_or(&alias.name)
                            )
                        }),
                );
            }
            _ => {}
        }
    }
    let source = parsed.text.text.as_str();
    let mut specs = AsyncSpecs::new();
    for statement in &tree.body {
        let Stmt::ClassDef(node) = statement else {
            continue;
        };
        let methods = node
            .body
            .iter()
            .filter_map(|member| match member {
                Stmt::FunctionDef(method)
                    if method.is_async
                        && method.decorator_list.iter().all(|decorator| {
                            decorators.contains(&unparse(source, &decorator.expression))
                        }) =>
                {
                    Some(method.name.to_string())
                }
                _ => None,
            })
            .collect();
        specs.insert(format!("{module}.{}", node.name), methods);
    }
    specs
}

fn declared_fixtures<'a>(
    source: (&'a SourceFile, &'a ParsedSource),
    constructors: &HashSet<String>,
    inherited: &Fixtures,
    specs: SpecView<'_>,
) -> Fixtures {
    let helpers = HashMap::new();
    let module = TestModule::new(source.0.path.as_str(), source.1);
    let mut visitor = TestVisitor::new(module, (constructors, inherited), specs, &helpers, true);
    visitor.visit_module();
    visitor.defined_fixtures
}

fn analyze_module<'a>(
    source: (&'a SourceFile, &'a ParsedSource),
    constructors: &HashSet<String>,
    fixtures: &Fixtures,
    shared: &AsyncSpecs,
) -> TestModule<'a> {
    let (file, parsed) = source;
    let own = class_async_methods(parsed, &dotted_module(&file.path));
    let specs = SpecView {
        shared,
        own: Some(&own),
    };
    let definitions = local_helpers(parsed);
    let mut merged = fixtures.clone();
    merged.extend(declared_fixtures(source, constructors, fixtures, specs));
    let module = TestModule::new(file.path.as_str(), parsed);
    let mut visitor = TestVisitor::new(module, (constructors, &merged), specs, &definitions, false);
    visitor.bind_module_helpers();
    visitor.visit_module();
    let mut result = visitor.result;
    result.resolve_helpers();
    result
}

/// Analyze test files, in path order, against the project's sources.
pub fn analyze<'a>(
    sources: &[(&'a SourceFile, &'a ParsedSource)],
    tests: &[(&'a SourceFile, &'a ParsedSource)],
) -> Vec<TestModule<'a>> {
    let mut source_specs = AsyncSpecs::new();
    for (source, parsed) in sources {
        source_specs.extend(class_async_methods(parsed, &source.module));
    }
    let mut constructors: HashSet<String> = source_specs.keys().cloned().collect();
    let mut async_specs = source_specs.clone();
    if !source_specs.is_empty() {
        let namespaces: HashMap<&str, Namespace> = sources
            .iter()
            .map(|(source, parsed)| {
                let namespace = namespace_of(
                    &parsed.text.text,
                    parsed.module(),
                    &source.module,
                    source.is_package,
                );
                (source.module.as_str(), namespace)
            })
            .collect();
        for (module, namespace) in &namespaces {
            for name in namespace.imported.keys() {
                let alias = format!("{module}.{name}");
                if let Some((target_module, Some(target_name))) =
                    resolve_dotted(&alias, &namespaces)
                    && let Some(methods) =
                        source_specs.get(&format!("{target_module}.{target_name}"))
                {
                    constructors.insert(alias.clone());
                    async_specs.insert(alias, methods.clone());
                }
            }
        }
    }
    let mut conftests: Vec<&(&SourceFile, &ParsedSource)> = tests
        .iter()
        .filter(|(file, _)| file.path.ends_with("conftest.py"))
        .collect();
    conftests.sort_by_key(|(file, _)| (file.path.matches('/').count(), file.path.as_str()));
    let mut scopes: Vec<(String, Fixtures)> = Vec::new();
    for (file, parsed) in conftests {
        let mut inherited = Fixtures::new();
        for (scope, bindings) in &scopes {
            if file.path.starts_with(scope.as_str()) {
                inherited.extend(bindings.iter().map(|(k, v)| (k.clone(), Arc::clone(v))));
            }
        }
        async_specs.extend(class_async_methods(parsed, &dotted_module(&file.path)));
        let specs = SpecView {
            shared: &async_specs,
            own: None,
        };
        let declared = declared_fixtures((file, parsed), &constructors, &inherited, specs);
        let scope = file
            .path
            .strip_suffix("conftest.py")
            .unwrap_or(&file.path)
            .to_owned();
        scopes.push((scope, declared));
    }
    scopes.sort_by_key(|(scope, _)| scope.len());
    tests
        .par_iter()
        .map(|(file, parsed)| {
            let mut fixtures = Fixtures::new();
            for (scope, bindings) in &scopes {
                if file.path.starts_with(scope.as_str()) {
                    fixtures.extend(bindings.iter().map(|(k, v)| (k.clone(), Arc::clone(v))));
                }
            }
            analyze_module((file, parsed), &constructors, &fixtures, &async_specs)
        })
        .collect()
}
