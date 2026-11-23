//! LAY006: `layout/callee-before-caller`.
use super::{Finding, RuleInput};
use crate::checker::analysis::edits::{fix, python_strip};
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    ExceptHandler, Expr, ExprContext, Parameters, Pattern, Stmt, StmtFunctionDef,
};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::{BTreeSet, HashMap, HashSet};

const HINT: &str = "Keep the high-level caller above same-visibility helpers after reviewing definition-time dependencies and execution boundaries.";

struct Issue<'a> {
    node: &'a StmtFunctionDef,
    scope: &'a str,
    callees: Vec<(&'a str, usize)>,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let mut issues = scope_issues(input, &module.body, "module", false);
    for statement in &module.body {
        if let Stmt::ClassDef(node) = statement
            && node.bases().is_empty()
            && node.decorator_list.is_empty()
        {
            issues.extend(scope_issues(input, &node.body, node.name.as_str(), true));
        }
    }
    issues
        .into_iter()
        .map(|issue| {
            let repair = rewrite(input, &issue);
            let (line, column) = input.place(issue.node.start());
            let callees: Vec<Value> = issue
                .callees
                .iter()
                .map(|(name, line)| json!({"name": name, "line": line}))
                .collect();
            input.violation(Finding {
                message: format!(
                    "review placing {} before the helpers it calls",
                    issue.node.name
                ),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "scope": issue.scope,
                    "caller": issue.node.name.as_str(),
                    "callees": callees,
                    "automatic_rewrite": repair.is_some(),
                }),
                hint: HINT.to_owned(),
                fix: repair,
            })
        })
        .collect()
}

fn scope_issues<'a>(
    input: &RuleInput<'_>,
    body: &'a [Stmt],
    scope: &'a str,
    methods: bool,
) -> Vec<Issue<'a>> {
    let bound = bindings(body);
    if bound.contains_key("*") {
        return Vec::new();
    }
    let functions: Vec<&StmtFunctionDef> = body
        .iter()
        .filter_map(Stmt::as_function_def_stmt)
        .filter(|node| {
            bound.get(node.name.as_str()) == Some(&1)
                && node.decorator_list.is_empty()
                && !node.name.starts_with("__")
        })
        .collect();
    let index: HashMap<&str, usize> = functions
        .iter()
        .enumerate()
        .map(|(position, node)| (node.name.as_str(), position))
        .collect();
    let graph: Vec<Vec<usize>> = functions
        .iter()
        .map(|node| {
            callees(node, methods)
                .into_iter()
                .filter_map(|name| index.get(name).copied())
                .collect()
        })
        .collect();
    let components = strongly_connected(&graph);
    let line_of = |node: &StmtFunctionDef| input.place(node.start()).0;
    let mut issues = Vec::new();
    for (position, caller) in functions.iter().copied().enumerate() {
        let prerequisites = prerequisite_names(caller.parameters.as_ref(), caller);
        let private = caller.name.starts_with('_');
        let mut earlier: Vec<(&str, usize)> = graph[position]
            .iter()
            .filter(|callee| {
                let node = functions[**callee];
                line_of(node) < line_of(caller)
                    && components[**callee] != components[position]
                    && !prerequisites.contains(node.name.as_str())
                    && node.name.starts_with('_') == private
            })
            .map(|callee| {
                let node = functions[*callee];
                (node.name.as_str(), line_of(node))
            })
            .collect();
        earlier.sort_unstable();
        if !earlier.is_empty() {
            issues.push(Issue {
                node: caller,
                scope,
                callees: earlier,
            });
        }
    }
    issues
}

/// Names the `def` statement needs bound, which a move must not pass.
fn prerequisite_names<'a>(
    parameters: &'a Parameters,
    function: &'a StmtFunctionDef,
) -> HashSet<&'a str> {
    let mut names = NameReads {
        names: HashSet::new(),
    };
    for item in parameters.iter_non_variadic_params() {
        if let Some(default) = item.default.as_deref() {
            names.visit_expr(default);
        }
        if let Some(annotation) = item.parameter.annotation() {
            names.visit_expr(annotation);
        }
    }
    if let Some(returns) = function.returns.as_deref() {
        names.visit_expr(returns);
    }
    names.names
}

/// Names a `def` statement reads when it runs: defaults and annotations.
fn eager_names(function: &StmtFunctionDef) -> HashSet<&str> {
    let parameters = function.parameters.as_ref();
    let mut names = prerequisite_names(parameters, function);
    let mut variadic = NameReads {
        names: HashSet::new(),
    };
    for parameter in [parameters.vararg.as_deref(), parameters.kwarg.as_deref()]
        .into_iter()
        .flatten()
    {
        if let Some(annotation) = parameter.annotation() {
            variadic.visit_expr(annotation);
        }
    }
    names.extend(variadic.names);
    names
}

struct NameReads<'a> {
    names: HashSet<&'a str>,
}

impl<'a> Visitor<'a> for NameReads<'a> {
    fn visit_expr(&mut self, node: &'a Expr) {
        if let Expr::Name(item) = node {
            self.names.insert(item.id.as_str());
        }
        visitor::walk_expr(self, node);
    }
}

fn callees(node: &StmtFunctionDef, methods: bool) -> BTreeSet<&str> {
    let mut local = bindings(&node.body);
    let parameters = node.parameters.as_ref();
    for item in parameters.iter_non_variadic_params() {
        *local.entry(item.parameter.name.as_str()).or_insert(0) += 1;
    }
    for parameter in [parameters.vararg.as_deref(), parameters.kwarg.as_deref()]
        .into_iter()
        .flatten()
    {
        *local.entry(parameter.name.as_str()).or_insert(0) += 1;
    }
    let receiver = if methods {
        parameters
            .args
            .first()
            .map(|item| item.parameter.name.as_str())
    } else {
        None
    };
    let rebound = receiver.is_some_and(|name| local.get(name).copied().unwrap_or(0) != 1);
    let mut calls = BodyCalls {
        local: &local,
        methods,
        receiver: if rebound { None } else { receiver },
        found: BTreeSet::new(),
    };
    calls.visit_body(&node.body);
    calls.found
}

/// Direct calls in a body, outside nested scopes and comprehensions.
struct BodyCalls<'a, 'b> {
    local: &'b HashMap<&'a str, usize>,
    methods: bool,
    receiver: Option<&'a str>,
    found: BTreeSet<&'a str>,
}

impl<'a> Visitor<'a> for BodyCalls<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if !matches!(statement, Stmt::FunctionDef(_) | Stmt::ClassDef(_)) {
            visitor::walk_stmt(self, statement);
        }
    }

    fn visit_expr(&mut self, node: &'a Expr) {
        if is_nested_scope(node) {
            return;
        }
        if let Expr::Call(call) = node {
            match call.func.as_ref() {
                Expr::Name(name) if !self.methods && !self.local.contains_key(name.id.as_str()) => {
                    self.found.insert(name.id.as_str());
                }
                Expr::Attribute(attribute) if self.methods => {
                    if let Expr::Name(owner) = attribute.value.as_ref()
                        && self.receiver == Some(owner.id.as_str())
                    {
                        self.found.insert(attribute.attr.as_str());
                    }
                }
                _ => {}
            }
        }
        visitor::walk_expr(self, node);
    }
}

fn is_nested_scope(node: &Expr) -> bool {
    matches!(
        node,
        Expr::Lambda(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::Generator(_)
    )
}

fn alias_binding(alias: &ruff_python_ast::Alias) -> &str {
    match &alias.asname {
        Some(name) => name.as_str(),
        None => alias.name.split('.').next().unwrap_or_default(),
    }
}

fn bindings(body: &[Stmt]) -> HashMap<&str, usize> {
    let mut counter = BodyBindings {
        counts: HashMap::new(),
    };
    counter.visit_body(body);
    counter.counts
}

struct BodyBindings<'a> {
    counts: HashMap<&'a str, usize>,
}

impl<'a> BodyBindings<'a> {
    fn add(&mut self, name: &'a str) {
        *self.counts.entry(name).or_insert(0) += 1;
    }
}

impl<'a> Visitor<'a> for BodyBindings<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => {
                self.add(node.name.as_str());
                return;
            }
            Stmt::ClassDef(node) => {
                self.add(node.name.as_str());
                return;
            }
            Stmt::Import(node) => {
                for alias in &node.names {
                    self.add(alias_binding(alias));
                }
            }
            Stmt::ImportFrom(node) => {
                for alias in &node.names {
                    self.add(alias_binding(alias));
                }
            }
            Stmt::Global(node) => {
                for name in &node.names {
                    self.add(name.as_str());
                }
            }
            Stmt::Nonlocal(node) => {
                for name in &node.names {
                    self.add(name.as_str());
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, node: &'a Expr) {
        if is_nested_scope(node) {
            return;
        }
        if let Expr::Name(item) = node
            && item.ctx != ExprContext::Load
        {
            self.add(item.id.as_str());
        }
        visitor::walk_expr(self, node);
    }

    fn visit_except_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        if let Some(name) = &node.name {
            self.add(name.as_str());
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
        if let Some(name) = name {
            self.add(name.as_str());
        }
        visitor::walk_pattern(self, pattern);
    }
}

/// The component of each node, by Tarjan's algorithm.
fn strongly_connected(graph: &[Vec<usize>]) -> Vec<usize> {
    struct State<'g> {
        graph: &'g [Vec<usize>],
        order: Vec<Option<usize>>,
        low: Vec<usize>,
        on_stack: Vec<bool>,
        stack: Vec<usize>,
        component: Vec<usize>,
        counter: usize,
        components: usize,
    }

    fn visit(state: &mut State<'_>, node: usize) {
        state.order[node] = Some(state.counter);
        state.low[node] = state.counter;
        state.counter += 1;
        state.stack.push(node);
        state.on_stack[node] = true;
        let graph = state.graph;
        for &child in &graph[node] {
            match state.order[child] {
                None => {
                    visit(state, child);
                    state.low[node] = state.low[node].min(state.low[child]);
                }
                Some(order) if state.on_stack[child] => {
                    state.low[node] = state.low[node].min(order);
                }
                Some(_) => {}
            }
        }
        if Some(state.low[node]) == state.order[node] {
            while let Some(member) = state.stack.pop() {
                state.on_stack[member] = false;
                state.component[member] = state.components;
                if member == node {
                    break;
                }
            }
            state.components += 1;
        }
    }

    let size = graph.len();
    let mut state = State {
        graph,
        order: vec![None; size],
        low: vec![0; size],
        on_stack: vec![false; size],
        stack: Vec::new(),
        component: vec![0; size],
        counter: 0,
        components: 0,
    };
    for node in 0..size {
        if state.order[node].is_none() {
            visit(&mut state, node);
        }
    }
    state.component
}

/// Move the caller, with its leading comments, above its earliest helper.
///
/// Safe: an undecorated definition only evaluates its defaults and annotations,
/// and the move is refused when code it passes binds their names.
fn rewrite(input: &RuleInput<'_>, issue: &Issue<'_>) -> Option<Value> {
    let parsed = input.parsed;
    let text = &parsed.text;
    let module = parsed.module();
    let body: &[Stmt] = if issue.scope == "module" {
        &module.body
    } else {
        module.body.iter().find_map(|node| match node {
            Stmt::ClassDef(class) if class.name.as_str() == issue.scope => {
                Some(class.body.as_slice())
            }
            _ => None,
        })?
    };
    let caller = issue.node;
    if !text.stands_alone(caller.range()) {
        return None;
    }
    let first = issue.callees.iter().map(|(_, line)| *line).min()?;
    let target = body.iter().position(|statement| {
        matches!(statement, Stmt::FunctionDef(node)
            if input.place(parsed.function_start(node)).0 == first)
    })?;
    let caller_index = body.iter().position(
        |statement| matches!(statement, Stmt::FunctionDef(node) if std::ptr::eq(node, caller)),
    )?;
    let passed = body.get(target..caller_index)?;
    let eager = eager_names(caller);
    let mut bound = BoundNames {
        names: HashSet::new(),
    };
    for statement in passed {
        bound.visit_stmt(statement);
    }
    if !eager.is_disjoint(&bound.names) {
        return None;
    }
    let target_start = match &body[target] {
        Stmt::FunctionDef(node) => parsed.function_start(node),
        _ => return None,
    };
    let start = text.block_start(usize::from(target_start));
    let caller_start = text.block_start(usize::from(caller.start()));
    let caller_end = text.line_end_at(usize::from(caller.end()));
    let mut gap_start = caller_start;
    while gap_start > 0 {
        let previous = text.line_start(text.line_at(gap_start - 1));
        if !python_strip(&text.text[previous..gap_start]).is_empty() {
            break;
        }
        gap_start = previous;
    }
    if gap_start <= start {
        return None;
    }
    let mut block = text.text[caller_start..caller_end].to_owned();
    if !block.ends_with('\n') {
        block.push('\n');
    }
    block.push_str(&text.text[gap_start..caller_start]);
    Some(fix(
        "safe",
        &format!("Move {} above the helpers it calls", caller.name),
        vec![
            text.insert(start, &block),
            text.edit(gap_start, caller_end, ""),
        ],
    ))
}

/// Every name the statements could bind, including nested definitions.
struct BoundNames<'a> {
    names: HashSet<&'a str>,
}

impl<'a> Visitor<'a> for BoundNames<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => {
                self.names.insert(node.name.as_str());
            }
            Stmt::ClassDef(node) => {
                self.names.insert(node.name.as_str());
            }
            Stmt::Import(node) => self.names.extend(node.names.iter().map(alias_binding)),
            Stmt::ImportFrom(node) => self.names.extend(node.names.iter().map(alias_binding)),
            Stmt::Global(node) => self
                .names
                .extend(node.names.iter().map(|name| name.as_str())),
            Stmt::Nonlocal(node) => self
                .names
                .extend(node.names.iter().map(|name| name.as_str())),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, node: &'a Expr) {
        if let Expr::Name(item) = node
            && item.ctx != ExprContext::Load
        {
            self.names.insert(item.id.as_str());
        }
        visitor::walk_expr(self, node);
    }
}
