//! CPX006: `complexity/excess-comprehension-loops`.
use super::{Finding, RuleInput};
use crate::checker::analysis::dump::{dump_expr, sha256_hex};
use crate::checker::analysis::nodes::is_constant;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Comprehension, Expr};
use serde_json::json;

const DEFAULT_LIMIT: usize = 1;
const MAX_FILTER_OPERANDS: usize = 2;

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let limit = input.contracts.threshold(
        &input.source.module,
        "max_comprehension_loops",
        DEFAULT_LIMIT,
    );
    let module = input.parsed.module();
    let mut outermost = Outermost { found: Vec::new() };
    outermost.visit_body(&module.body);
    let source = input.parsed.text.text.as_str();
    let mut found = Vec::new();
    for node in outermost.found {
        let reasons = reasons(node, limit);
        if reasons.is_empty() {
            continue;
        }
        let range = input.parsed.comprehension_range(node);
        let (line, column) = input.place(range.start());
        let (end_line, end_column) = input.place(range.end());
        found.push(input.violation(Finding {
            message: format!("Complex comprehension: {}", reasons.join("; ")),
            line,
            column,
            end_line: Some(end_line),
            end_column: Some(end_column),
            expected: json!({
                "max_comprehension_loops": limit,
                "conditions": reasons,
                "comprehension_edges": [sha256_hex(&dump_expr(source, node))],
                "automatic_rewrite": false,
            }),
            hint: "Name the per-item step or use a loop; preserve lazy evaluation and iteration order.".to_owned(),
            fix: None,
        }));
    }
    found
}

/// The element and generators of a comprehension.
fn parts(node: &Expr) -> Option<(&Expr, &[Comprehension])> {
    match node {
        Expr::ListComp(item) => Some((&item.elt, &item.generators)),
        Expr::SetComp(item) => Some((&item.elt, &item.generators)),
        Expr::Generator(item) => Some((&item.elt, &item.generators)),
        Expr::DictComp(item) => Some((&item.value, &item.generators)),
        _ => None,
    }
}

struct Outermost<'a> {
    found: Vec<&'a Expr>,
}

impl<'a> Visitor<'a> for Outermost<'a> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if parts(expression).is_some() {
            self.found.push(expression);
        } else {
            visitor::walk_expr(self, expression);
        }
    }
}

fn reasons(root: &Expr, limit: usize) -> Vec<&'static str> {
    let mut collector = Reasons {
        limit,
        loops: false,
        conditional: false,
        filters: false,
    };
    collector.visit_expr(root);
    let mut reasons = Vec::new();
    if collector.filters {
        reasons.push("multiple or compound filters");
    }
    if collector.conditional {
        reasons.push("nontrivial conditional element");
    }
    if collector.loops {
        reasons.push("too many for clauses");
    }
    reasons
}

struct Reasons {
    limit: usize,
    loops: bool,
    conditional: bool,
    filters: bool,
}

impl<'a> Visitor<'a> for Reasons {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Some((element, generators)) = parts(expression) {
            self.loops |= generators.len() > self.limit;
            if let Expr::If(conditional) = element {
                self.conditional |=
                    !(is_trivial(&conditional.body) && is_trivial(&conditional.orelse));
            }
            self.filters |= generators.iter().any(|generator| {
                generator.ifs.len() > 1 || generator.ifs.iter().any(has_wide_boolean)
            });
        }
        visitor::walk_expr(self, expression);
    }
}

fn is_trivial(branch: &Expr) -> bool {
    matches!(branch, Expr::Name(_) | Expr::Attribute(_)) || is_constant(branch)
}

fn has_wide_boolean(condition: &Expr) -> bool {
    let mut finder = WideBoolean { found: false };
    finder.visit_expr(condition);
    finder.found
}

struct WideBoolean {
    found: bool,
}

impl<'a> Visitor<'a> for WideBoolean {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::BoolOp(node) = expression
            && node.values.len() > MAX_FILTER_OPERANDS
        {
            self.found = true;
        }
        visitor::walk_expr(self, expression);
    }
}
