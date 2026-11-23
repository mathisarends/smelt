//! CPX007: `complexity/excess-constructor-decisions`.
use super::{Finding, RuleInput};
use crate::checker::analysis::dump::{dump_elif, dump_expr, dump_stmt, sha256_hex};
use crate::checker::analysis::nodes::all_classes;
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::{Ranged, TextSize};
use serde_json::{Value, json};

const DEFAULT_LIMIT: usize = 2;
const HINT: &str = "Move a cohesive step into a named helper and assign its result; preserve initialization order and exceptions.";

struct Decision {
    kind: &'static str,
    offset: TextSize,
    digest: String,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let limit = input.contracts.threshold(
        &input.source.module,
        "max_constructor_decisions",
        DEFAULT_LIMIT,
    );
    let mut found = Vec::new();
    for owner in all_classes(input.parsed.module()) {
        for method in &owner.body {
            let Stmt::FunctionDef(method) = method else {
                continue;
            };
            if !matches!(method.name.as_str(), "__init__" | "__post_init__") {
                continue;
            }
            let mut collector = Decisions {
                parsed: input.parsed,
                found: Vec::new(),
            };
            collector.visit_body(&method.body);
            let mut decisions = collector.found;
            if decisions.len() <= limit {
                continue;
            }
            decisions.sort_by_key(|decision| decision.offset);
            let edges: Vec<String> = decisions
                .iter()
                .map(|item| format!("{}.{}:{}", owner.name, method.name, item.digest))
                .collect();
            let details: Vec<Value> = decisions
                .iter()
                .map(|item| {
                    let (line, column) = input.place(item.offset);
                    json!({"kind": item.kind, "line": line, "column": column})
                })
                .collect();
            let (line, column) = input.place(input.parsed.function_start(method));
            found.push(input.violation(Finding {
                message: format!(
                    "Constructor {}.{} contains {} decisions (limit {limit}); name its normalization or validation steps",
                    owner.name,
                    method.name,
                    decisions.len()
                ),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "class": owner.name.as_str(),
                    "method": method.name.as_str(),
                    "max_constructor_decisions": limit,
                    "count": decisions.len(),
                    "decision_edges": edges,
                    "decisions": details,
                    "automatic_rewrite": false,
                }),
                hint: HINT.to_owned(),
                fix: None,
            }));
        }
    }
    found
}

struct Decisions<'p> {
    parsed: &'p ParsedSource,
    found: Vec<Decision>,
}

impl Decisions<'_> {
    fn source(&self) -> &str {
        &self.parsed.text.text
    }

    fn statement(&mut self, kind: &'static str, statement: &Stmt) {
        let digest = sha256_hex(&dump_stmt(self.source(), statement));
        self.found.push(Decision {
            kind,
            offset: statement.start(),
            digest,
        });
    }

    fn expression(&mut self, kind: &'static str, expression: &Expr) {
        let digest = sha256_hex(&dump_expr(self.source(), expression));
        self.found.push(Decision {
            kind,
            offset: self.parsed.comprehension_range(expression).start(),
            digest,
        });
    }
}

impl<'a> Visitor<'a> for Decisions<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => return,
            Stmt::If(node) => {
                self.statement("If", statement);
                self.visit_expr(&node.test);
                self.visit_body(&node.body);
                for (index, clause) in node.elif_else_clauses.iter().enumerate() {
                    if let Some(test) = &clause.test {
                        let rest = &node.elif_else_clauses[index + 1..];
                        let digest =
                            sha256_hex(&dump_elif(self.source(), test, &clause.body, rest));
                        self.found.push(Decision {
                            kind: "If",
                            offset: clause.start(),
                            digest,
                        });
                        self.visit_expr(test);
                    }
                    self.visit_body(&clause.body);
                }
                return;
            }
            Stmt::For(node) => {
                self.statement(if node.is_async { "AsyncFor" } else { "For" }, statement);
            }
            Stmt::While(_) => self.statement("While", statement),
            Stmt::Try(node) => {
                self.statement(if node.is_star { "TryStar" } else { "Try" }, statement);
            }
            Stmt::Match(_) => self.statement("Match", statement),
            Stmt::Raise(_) => self.statement("Raise", statement),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Lambda(_) => return,
            Expr::ListComp(_) => self.expression("ListComp", expression),
            Expr::SetComp(_) => self.expression("SetComp", expression),
            Expr::DictComp(_) => self.expression("DictComp", expression),
            Expr::Generator(_) => self.expression("GeneratorExp", expression),
            Expr::If(_) => self.expression("IfExp", expression),
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
}
