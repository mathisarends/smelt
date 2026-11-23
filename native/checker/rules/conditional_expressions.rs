//! CPX005: `complexity/forbidden-nested-conditional`.
use super::{Finding, RuleInput};
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::source_order::{SourceOrderVisitor, TraversalSignal};
use ruff_python_ast::{AnyNodeRef, Expr, ExprIf};
use ruff_text_size::{Ranged, TextRange};
use serde_json::json;
use std::collections::HashSet;

#[derive(Default)]
struct Conditionals<'a> {
    found: Vec<&'a ExprIf>,
}

impl<'a> SourceOrderVisitor<'a> for Conditionals<'a> {
    fn enter_node(&mut self, node: AnyNodeRef<'a>) -> TraversalSignal {
        if let AnyNodeRef::ExprIf(expression) = node {
            self.found.push(expression);
        }
        TraversalSignal::Traverse
    }
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let mut conditionals = Conditionals::default();
    conditionals.visit_body(&input.parsed.module().body);
    let nested: HashSet<TextRange> = conditionals
        .found
        .iter()
        .flat_map(|node| [node.body.as_ref(), node.orelse.as_ref()])
        .filter(|branch| matches!(branch, Expr::If(_)))
        .map(Ranged::range)
        .collect();
    let text = &input.parsed.text;
    conditionals
        .found
        .iter()
        .filter(|node| !nested.contains(&node.range()))
        .filter(|node| matches!(*node.body, Expr::If(_)) || matches!(*node.orelse, Expr::If(_)))
        .map(|node| {
            let (line, column) = text.position(usize::from(node.start()));
            let (end_line, end_column) = text.position(usize::from(node.end()));
            input.violation(Finding {
                message: "This conditional expression contains multiple decision branches; \
                          use explicit if/elif or match statements"
                    .to_owned(),
                line,
                column: column + 1,
                end_line: Some(end_line),
                end_column: Some(end_column + 1),
                expected: json!({"automatic_rewrite": false}),
                hint: "Preserve lazy branch evaluation and branch order in the statements."
                    .to_owned(),
                fix: None,
            })
        })
        .collect()
}
