//! Bounded, same-file helper bindings and explicit coroutine execution.
use super::TestFunction;
use super::deadlines::{async_deadline, in_body};
use crate::checker::analysis::walk::{TreeNode, children, expression_key, nodes_below};
use crate::checker::source::ParsedSource;
use ruff_python_ast::{ExceptHandler, Expr, ExprContext, Pattern, Stmt, StmtFunctionDef};
use std::collections::{HashMap, HashSet};

const COUNTED_TIMEOUTS: [&str; 4] = [
    "asyncio.timeout",
    "asyncio.timeout_at",
    "anyio.fail_after",
    "trio.fail_after",
];

/// Only unconditional, undecorated, non-generator, unambiguous definitions.
pub fn local_helpers(parsed: &ParsedSource) -> HashMap<usize, &StmtFunctionDef> {
    let module = parsed.module();
    let tree = crate::checker::analysis::walk::tree_nodes(module);
    let mut dynamic_writes: HashSet<&str> = HashSet::new();
    for node in &tree {
        match node {
            TreeNode::Statement(Stmt::Global(item)) => {
                dynamic_writes.extend(item.names.iter().map(|name| name.as_str()));
            }
            TreeNode::Statement(Stmt::Nonlocal(item)) => {
                dynamic_writes.extend(item.names.iter().map(|name| name.as_str()));
            }
            _ => {}
        }
    }
    let mut result = HashMap::new();
    collect_scope(parsed, &module.body, None, &dynamic_writes, &mut result);
    for node in &tree {
        if let TreeNode::Statement(Stmt::FunctionDef(function)) = node {
            collect_scope(
                parsed,
                &function.body,
                Some(function),
                &dynamic_writes,
                &mut result,
            );
        }
    }
    result
}

fn collect_scope<'a>(
    parsed: &ParsedSource,
    body: &'a [Stmt],
    function: Option<&'a StmtFunctionDef>,
    dynamic_writes: &HashSet<&str>,
    result: &mut HashMap<usize, &'a StmtFunctionDef>,
) {
    let bindings = scope_bindings(body, function);
    if bindings.contains_key("*") {
        return;
    }
    for statement in body {
        let Stmt::FunctionDef(node) = statement else {
            continue;
        };
        let name = node.name.as_str();
        if name.starts_with("test_")
            || !node.decorator_list.is_empty()
            || bindings.get(name).copied().unwrap_or(0) != 1
            || dynamic_writes.contains(name)
        {
            continue;
        }
        let nodes = scope_nodes(&node.body);
        let generator = nodes.iter().any(|child| {
            matches!(
                child,
                TreeNode::Expression(Expr::Yield(_) | Expr::YieldFrom(_))
            )
        });
        let unstable = nodes.iter().any(|child| {
            matches!(child, TreeNode::Expression(Expr::Name(item))
                if item.ctx == ExprContext::Load
                    && (dynamic_writes.contains(item.id.as_str())
                        || bindings.get(item.id.as_str()).copied().unwrap_or(0) > 1))
        });
        if generator || unstable {
            continue;
        }
        let (line, _) = parsed
            .text
            .position(usize::from(parsed.function_start(node)));
        result.insert(line, node);
    }
}

fn scope_bindings<'a>(
    body: &'a [Stmt],
    function: Option<&'a StmtFunctionDef>,
) -> HashMap<&'a str, usize> {
    let mut bindings: HashMap<&str, usize> = HashMap::new();
    for node in scope_nodes(body) {
        let mut count = |name: &'a str| *bindings.entry(name).or_insert(0) += 1;
        match node {
            TreeNode::Expression(Expr::Name(item))
                if matches!(item.ctx, ExprContext::Store | ExprContext::Del) =>
            {
                count(item.id.as_str());
            }
            TreeNode::Statement(Stmt::FunctionDef(item)) => count(item.name.as_str()),
            TreeNode::Statement(Stmt::ClassDef(item)) => count(item.name.as_str()),
            TreeNode::Statement(Stmt::Import(item)) => {
                for alias in &item.names {
                    count(alias.asname.as_ref().map_or_else(
                        || alias.name.as_str().split('.').next().unwrap_or_default(),
                        |name| name.as_str(),
                    ));
                }
            }
            TreeNode::Statement(Stmt::ImportFrom(item)) => {
                for alias in &item.names {
                    count(alias.asname.as_ref().map_or_else(
                        || alias.name.as_str().split('.').next().unwrap_or_default(),
                        |name| name.as_str(),
                    ));
                }
            }
            TreeNode::Handler(ExceptHandler::ExceptHandler(item)) => {
                if let Some(name) = &item.name {
                    count(name.as_str());
                }
            }
            TreeNode::Pattern(Pattern::MatchAs(item)) => {
                if let Some(name) = &item.name {
                    count(name.as_str());
                }
            }
            TreeNode::Pattern(Pattern::MatchStar(item)) => {
                if let Some(name) = &item.name {
                    count(name.as_str());
                }
            }
            TreeNode::Pattern(Pattern::MatchMapping(item)) => {
                if let Some(rest) = &item.rest {
                    count(rest.as_str());
                }
            }
            _ => {}
        }
    }
    if let Some(function) = function {
        for node in nodes_below(TreeNode::Parameters(&function.parameters)) {
            if let TreeNode::Parameter(parameter) = node {
                *bindings.entry(parameter.name.as_str()).or_insert(0) += 1;
            }
        }
    }
    bindings
}

/// Nodes of one scope: a nested definition is seen, but not entered.
fn scope_nodes(body: &[Stmt]) -> Vec<TreeNode<'_>> {
    let mut pending: Vec<TreeNode<'_>> = body.iter().map(TreeNode::Statement).collect();
    let mut found = Vec::new();
    while let Some(node) = pending.pop() {
        found.push(node);
        if !matches!(
            node,
            TreeNode::Statement(Stmt::FunctionDef(_) | Stmt::ClassDef(_))
                | TreeNode::Expression(Expr::Lambda(_))
        ) {
            pending.extend(children(node));
        }
    }
    found
}

pub fn helper_executes<'a>(
    helper: &StmtFunctionDef,
    call: &'a Expr,
    function: &TestFunction<'a>,
) -> bool {
    if !helper.is_async || async_deadline(call, function, &[]).awaited {
        return true;
    }
    matches!(
        function.parent(TreeNode::Expression(call)),
        Some(TreeNode::Expression(parent @ Expr::Call(outer)))
            if function.call_name(parent) == Some("asyncio.run")
                && outer
                    .arguments
                    .args
                    .first()
                    .is_some_and(|first| expression_key(first) == expression_key(call))
    )
}

pub fn outcome_propagates<'a>(node: TreeNode<'a>, function: &TestFunction<'a>) -> bool {
    let mut child = node;
    while let Some(parent) = function.parent(child) {
        match parent {
            TreeNode::Statement(Stmt::Try(_)) => return false,
            TreeNode::Statement(Stmt::With(with))
                if in_body(&with.body, child)
                    && with.items.iter().any(|item| {
                        !item.context_expr.is_call_expr()
                            || function
                                .call_name(&item.context_expr)
                                .is_none_or(|name| !COUNTED_TIMEOUTS.contains(&name))
                    }) =>
            {
                return false;
            }
            _ => {}
        }
        child = parent;
    }
    true
}
