//! Call-only assertions on known async mocks, with bounded local evidence.
use super::{MockObservation, ObservationKind, TestFunction};
use crate::checker::analysis::repr::int_repr;
use crate::checker::analysis::walk::{NodeKey, TreeNode, expression_key, nodes_below};
use crate::checker::source::ParsedSource;
use ruff_python_ast::{CmpOp, Expr, ExprAttribute, ExprContext, Number, Stmt, UnaryOp};
use ruff_text_size::Ranged;
use std::collections::{HashMap, HashSet};

pub fn call_only_async_mocks<'t, 'a>(
    test: &'t TestFunction<'a>,
    parsed: &ParsedSource,
) -> Vec<&'t MockObservation<'a>> {
    let asserted = asserted_nodes(test, parsed);
    let observations: Vec<&MockObservation<'a>> = test
        .mock_observations
        .iter()
        .filter(|item| item.node.is_call_expr() || asserted.contains(&expression_key(item.node)))
        .collect();
    let awaited: HashSet<&str> = observations
        .iter()
        .filter(|item| item.kind == ObservationKind::Await)
        .map(|item| item.origin.as_str())
        .collect();
    let mut risks: Vec<&MockObservation<'a>> = Vec::new();
    for item in observations {
        if item.kind != ObservationKind::Call || awaited.contains(item.origin.as_str()) {
            continue;
        }
        if let Expr::Attribute(attribute) = item.node
            && checks_no_call(item.node, attribute, test)
        {
            continue;
        }
        if !risks.iter().any(|risk| risk.origin == item.origin) {
            risks.push(item);
        }
    }
    risks
}

fn asserted_nodes(test: &TestFunction<'_>, parsed: &ParsedSource) -> HashSet<NodeKey> {
    let walked = nodes_below(TreeNode::Statement(test.statement));
    let mut writes: HashMap<&str, usize> = HashMap::new();
    let mut assignments: HashMap<&str, Vec<&Expr>> = HashMap::new();
    for node in &walked {
        match node {
            TreeNode::Expression(Expr::Name(name))
                if matches!(name.ctx, ExprContext::Store | ExprContext::Del) =>
            {
                *writes.entry(name.id.as_str()).or_insert(0) += 1;
            }
            TreeNode::Statement(Stmt::Assign(item)) => {
                for target in &item.targets {
                    record_assignment(target, &item.value, &mut assignments);
                }
            }
            TreeNode::Statement(Stmt::AnnAssign(item)) => {
                if let Some(value) = item.value.as_deref() {
                    record_assignment(&item.target, value, &mut assignments);
                }
            }
            _ => {}
        }
    }
    // Follow only single-assignment local values, such as unpacked call_args.
    let mut pending: Vec<TreeNode<'_>> = test
        .assertions
        .iter()
        .map(|statement| TreeNode::Statement(statement))
        .collect();
    pending.extend(
        test.calls
            .iter()
            .filter(|call| call.qualified.starts_with("unittest.TestCase.assert"))
            .map(|call| TreeNode::Expression(call.node)),
    );
    let line = |node: &Expr| parsed.text.position(usize::from(node.start())).0;
    let mut asserted = HashSet::new();
    let mut followed: HashSet<&str> = HashSet::new();
    while let Some(expression) = pending.pop() {
        for node in nodes_below(expression) {
            asserted.insert(node.key());
            if let TreeNode::Expression(found @ Expr::Name(name)) = node
                && followed.insert(name.id.as_str())
                && let Some([value]) = assignments.get(name.id.as_str()).map(Vec::as_slice)
                && writes.get(name.id.as_str()) == Some(&1)
                && line(value) < line(found)
            {
                pending.push(TreeNode::Expression(value));
            }
        }
    }
    asserted
}

fn record_assignment<'a>(
    target: &'a Expr,
    value: &'a Expr,
    assignments: &mut HashMap<&'a str, Vec<&'a Expr>>,
) {
    for node in nodes_below(TreeNode::Expression(target)) {
        if let TreeNode::Expression(Expr::Name(name)) = node
            && name.ctx == ExprContext::Store
        {
            assignments.entry(name.id.as_str()).or_default().push(value);
        }
    }
}

fn checks_no_call<'a>(node: &'a Expr, attribute: &ExprAttribute, test: &TestFunction<'a>) -> bool {
    match test.parent(TreeNode::Expression(node)) {
        Some(TreeNode::Expression(Expr::UnaryOp(parent))) if parent.op == UnaryOp::Not => true,
        Some(TreeNode::Expression(Expr::Compare(parent))) if parent.ops.len() == 1 => {
            let (left, right) = (parent.first_operand(), &parent.comparators()[0]);
            let key = expression_key(node);
            let other = if expression_key(left) == key {
                Some(right)
            } else if expression_key(right) == key {
                Some(left)
            } else {
                None
            };
            matches!(parent.ops[0], CmpOp::Eq | CmpOp::Is)
                && other.is_some_and(|other| {
                    is_empty_constant(other)
                        || (attribute.attr.as_str() == "call_args_list"
                            && matches!(other, Expr::List(list) if list.elts.is_empty()))
                })
        }
        _ => false,
    }
}

/// `value in (None, False, 0)` for a constant.
fn is_empty_constant(node: &Expr) -> bool {
    match node {
        Expr::NoneLiteral(_) => true,
        Expr::BooleanLiteral(item) => !item.value,
        Expr::NumberLiteral(item) => match &item.value {
            Number::Int(value) => int_repr(value).trim_start_matches('0').is_empty(),
            Number::Float(value) => *value == 0.0,
            Number::Complex { real, imag } => *real == 0.0 && *imag == 0.0,
        },
        _ => false,
    }
}
