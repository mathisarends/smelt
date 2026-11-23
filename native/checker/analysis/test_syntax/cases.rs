//! Narrow candidates for independent literal test cases, never lifecycle replay.
use super::TestFunction;
use crate::checker::analysis::nodes::is_constant;
use crate::checker::analysis::walk::{TreeNode, nodes_below};
use ruff_python_ast::{Expr, ExprContext, Stmt, StmtFor};
use std::collections::HashSet;

const MIN_CASES: usize = 2;
const MAX_CASES: usize = 12;

pub fn independent_case_loops<'a>(test: &TestFunction<'a>) -> Vec<&'a StmtFor> {
    test.node
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::For(node) if !node.is_async && node.orelse.is_empty() => Some(node),
            _ => None,
        })
        .filter(|node| {
            let cases = match node.iter.as_ref() {
                Expr::List(item) => &item.elts,
                Expr::Tuple(item) => &item.elts,
                _ => return false,
            };
            (MIN_CASES..=MAX_CASES).contains(&cases.len())
                && cases.iter().all(is_literal)
                && independent_body(node)
        })
        .collect()
}

fn is_literal(node: &Expr) -> bool {
    match node {
        Expr::List(item) => item.elts.iter().all(is_literal),
        Expr::Tuple(item) => item.elts.iter().all(is_literal),
        // A `**` entry has no key node, so only its value counts.
        Expr::Dict(item) => item
            .items
            .iter()
            .all(|entry| entry.key.as_ref().is_none_or(is_literal) && is_literal(&entry.value)),
        other => is_constant(other),
    }
}

fn independent_body(loop_node: &StmtFor) -> bool {
    let mut assigned: HashSet<&str> = nodes_below(TreeNode::Expression(&loop_node.target))
        .into_iter()
        .filter_map(|node| match node {
            TreeNode::Expression(Expr::Name(name)) => Some(name.id.as_str()),
            _ => None,
        })
        .collect();
    let locals_in_loop: HashSet<&str> = loop_node
        .body
        .iter()
        .flat_map(|statement| nodes_below(TreeNode::Statement(statement)))
        .filter_map(|node| match node {
            TreeNode::Expression(Expr::Name(name)) if name.ctx == ExprContext::Store => {
                Some(name.id.as_str())
            }
            _ => None,
        })
        .collect();
    let (mut action, mut assertion) = (false, false);
    for statement in &loop_node.body {
        let (targets, value): (&[Expr], Option<&Expr>) = match statement {
            Stmt::Assign(item) => (&item.targets, Some(&item.value)),
            Stmt::AnnAssign(item) => (
                std::slice::from_ref(item.target.as_ref()),
                item.value.as_deref(),
            ),
            Stmt::Assert(item) => (&[], Some(&item.test)),
            _ => return false,
        };
        if targets.iter().any(|target| !target.is_name_expr()) {
            return false;
        }
        let Some(value) = value else {
            return false;
        };
        let nodes = nodes_below(TreeNode::Expression(value));
        for child in &nodes {
            let TreeNode::Expression(expression) = child else {
                continue;
            };
            match expression {
                Expr::Name(name)
                    if name.ctx == ExprContext::Load
                        && locals_in_loop.contains(name.id.as_str())
                        && !assigned.contains(name.id.as_str()) =>
                {
                    return false;
                }
                Expr::Call(call) => {
                    // A method on pre-existing state can participate in replay/handoff.
                    if let Expr::Attribute(function) = call.func.as_ref() {
                        let known = matches!(function.value.as_ref(), Expr::Name(receiver)
                            if assigned.contains(receiver.id.as_str()));
                        if !known {
                            return false;
                        }
                    }
                }
                _ => {}
            }
        }
        assertion |= matches!(statement, Stmt::Assert(_));
        action |= nodes
            .iter()
            .any(|child| matches!(child, TreeNode::Expression(Expr::Call(_))));
        assigned.extend(
            targets
                .iter()
                .filter_map(Expr::as_name_expr)
                .map(|name| name.id.as_str()),
        );
    }
    action && assertion
}
