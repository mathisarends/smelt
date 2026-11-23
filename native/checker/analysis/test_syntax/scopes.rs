//! The names statements bind, with the reach of the Python analysis.
use crate::checker::analysis::walk::{TreeNode, children, nodes_below};
use ruff_python_ast::{Alias, ExceptHandler, Expr, ExprContext, Pattern, Stmt};
use std::collections::HashSet;

fn is_write(context: ExprContext) -> bool {
    matches!(context, ExprContext::Store | ExprContext::Del)
}

fn alias_name(alias: &Alias) -> &str {
    match &alias.asname {
        Some(name) => name.as_str(),
        None => alias
            .name
            .as_str()
            .split('.')
            .next()
            .unwrap_or(alias.name.as_str()),
    }
}

/// `_names`: names a target stores or deletes.
pub fn stored_names(target: &Expr) -> Vec<&str> {
    nodes_below(TreeNode::Expression(target))
        .into_iter()
        .filter_map(|node| match node {
            TreeNode::Expression(Expr::Name(name)) if is_write(name.ctx) => Some(name.id.as_str()),
            _ => None,
        })
        .collect()
}

/// `_locals`: names bound by statements, without entering nested definitions.
pub fn local_names<'a>(nodes: &[TreeNode<'a>]) -> HashSet<&'a str> {
    let mut found = HashSet::new();
    for node in nodes {
        collect_locals(*node, &mut found);
    }
    found
}

fn collect_locals<'a>(node: TreeNode<'a>, found: &mut HashSet<&'a str>) {
    if let TreeNode::Handler(ExceptHandler::ExceptHandler(handler)) = node
        && let Some(name) = &handler.name
    {
        found.insert(name.as_str());
    }
    match node {
        TreeNode::Statement(Stmt::FunctionDef(item)) => {
            found.insert(item.name.as_str());
        }
        TreeNode::Statement(Stmt::ClassDef(item)) => {
            found.insert(item.name.as_str());
        }
        TreeNode::Statement(Stmt::Import(item)) => {
            found.extend(item.names.iter().map(alias_name));
        }
        TreeNode::Statement(Stmt::ImportFrom(item)) => {
            found.extend(item.names.iter().map(|alias| {
                alias
                    .asname
                    .as_ref()
                    .map_or_else(|| alias_name(alias), |name| name.as_str())
            }));
        }
        _ => {
            for child in children(node) {
                match child {
                    TreeNode::Statement(_) | TreeNode::Elif(_) => collect_locals(child, found),
                    other => collect_expression_bindings(other, found),
                }
            }
        }
    }
}

/// `_expression_bindings`: names written below a node that is not a statement.
///
/// It stops at a named capture pattern and at scopes of their own, but does enter
/// the statements of a handler or a match case.
pub fn expression_bindings<'a>(node: TreeNode<'a>) -> HashSet<&'a str> {
    let mut found = HashSet::new();
    collect_expression_bindings(node, &mut found);
    found
}

fn collect_expression_bindings<'a>(node: TreeNode<'a>, found: &mut HashSet<&'a str>) {
    match node {
        TreeNode::Pattern(Pattern::MatchAs(item)) if item.name.is_some() => {
            found.extend(item.name.as_ref().map(|name| name.as_str()));
            return;
        }
        TreeNode::Pattern(Pattern::MatchStar(item)) if item.name.is_some() => {
            found.extend(item.name.as_ref().map(|name| name.as_str()));
            return;
        }
        TreeNode::Expression(
            Expr::Lambda(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::Generator(_),
        ) => return,
        TreeNode::Expression(Expr::Name(name)) if is_write(name.ctx) => {
            found.insert(name.id.as_str());
        }
        _ => {}
    }
    for child in children(node) {
        collect_expression_bindings(child, found);
    }
}
