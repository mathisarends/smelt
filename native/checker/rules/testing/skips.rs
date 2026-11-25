//! TST013: skips, expected failures and reruns that name no tracked reason.
use crate::checker::analysis::bindings::{qualified_name, unshadowed_bindings};
use crate::checker::analysis::comments::Comments;
use crate::checker::analysis::test_syntax::TestModule;
use regex::Regex;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprCall, Stmt};
use ruff_text_size::{Ranged, TextSize};
use std::collections::HashMap;

/// How a marker or call switches a test off.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Skip,
    Xfail,
    Rerun,
}

impl Kind {
    pub fn label(self) -> &'static str {
        match self {
            Self::Skip => "skip",
            Self::Xfail => "xfail",
            Self::Rerun => "rerun",
        }
    }
}

/// Where the reason of a form is, besides a `reason=` keyword.
#[derive(Clone, Copy)]
enum Shape {
    /// The reason is the first positional argument: `pytest.skip("why")`.
    ReasonFirst,
    /// A condition comes first and the reason second: `unittest.skipIf(cond, "why")`.
    ConditionFirst,
    /// An optional condition comes first; the reason is a keyword: `pytest.mark.skipif`.
    KeywordReason,
    /// No reason argument; only a comment can explain it.
    Bare,
}

const FORMS: [(&str, Kind, Shape); 12] = [
    ("pytest.mark.skip", Kind::Skip, Shape::ReasonFirst),
    ("pytest.mark.skipif", Kind::Skip, Shape::KeywordReason),
    ("pytest.mark.xfail", Kind::Xfail, Shape::KeywordReason),
    ("pytest.mark.flaky", Kind::Rerun, Shape::KeywordReason),
    ("pytest.skip", Kind::Skip, Shape::ReasonFirst),
    ("pytest.xfail", Kind::Xfail, Shape::ReasonFirst),
    ("unittest.skip", Kind::Skip, Shape::ReasonFirst),
    ("unittest.skipIf", Kind::Skip, Shape::ConditionFirst),
    ("unittest.skipUnless", Kind::Skip, Shape::ConditionFirst),
    ("unittest.expectedFailure", Kind::Xfail, Shape::Bare),
    ("flaky.flaky", Kind::Rerun, Shape::Bare),
    ("pytest_rerunfailures.flaky", Kind::Rerun, Shape::Bare),
];

/// A form that switched a test off without the reason it needs.
pub struct UntrackedSkip {
    pub offset: TextSize,
    pub form: &'static str,
    pub kind: Kind,
    /// The reason as written, when it is a string literal.
    pub reason: Option<String>,
    /// A platform, dependency or other runtime condition: a reason is enough.
    pub environment: bool,
}

/// The forms in a test module whose reason is missing or, for a quarantine, untracked.
pub fn untracked_skips(module: &TestModule<'_>, tracked: &Regex) -> Vec<UntrackedSkip> {
    let parsed = module.parsed;
    let tree = parsed.module();
    let source = parsed.text.text.as_str();
    if !source.contains("skip")
        && !source.contains("xfail")
        && !source.contains("expectedFailure")
        && !source.contains("flaky")
    {
        return Vec::new();
    }
    let bindings = unshadowed_bindings(source, tree, &[]);
    let mut collector = Collector {
        bindings: &bindings,
        conditional: 0,
        found: Vec::new(),
    };
    collector.visit_body(&tree.body);
    let comments = Comments::scan(parsed);
    collector
        .found
        .into_iter()
        .filter_map(|found| {
            let line = module.line(found.offset);
            let commented = std::iter::once(line)
                .chain(comments.block_above(line))
                .filter_map(|line| comments.text(line))
                .any(|text| tracked.is_match(text));
            let satisfied = match &found.reason {
                Reason::Missing => commented,
                Reason::Dynamic => true,
                Reason::Text(text) => {
                    (found.environment && !text.trim().is_empty())
                        || tracked.is_match(text)
                        || commented
                }
            };
            (!satisfied).then_some(UntrackedSkip {
                offset: found.offset,
                form: found.form,
                kind: found.kind,
                reason: match found.reason {
                    Reason::Text(text) => Some(text),
                    _ => None,
                },
                environment: found.environment,
            })
        })
        .collect()
}

enum Reason {
    Missing,
    /// A name, f-string or other expression: its text is not known statically.
    Dynamic,
    Text(String),
}

struct Use {
    offset: TextSize,
    form: &'static str,
    kind: Kind,
    reason: Reason,
    environment: bool,
}

struct Collector<'b> {
    bindings: &'b HashMap<String, String>,
    /// Nesting depth of `if` bodies and `except` handlers, where a skip is a runtime limit.
    conditional: usize,
    found: Vec<Use>,
}

impl Collector<'_> {
    fn form(&self, expression: &Expr) -> Option<(&'static str, Kind, Shape)> {
        let qualified = qualified_name(expression, self.bindings)?;
        FORMS
            .iter()
            .find(|(name, _, _)| *name == qualified)
            .copied()
    }

    fn record_call(&mut self, call: &ExprCall, form: (&'static str, Kind, Shape)) {
        let (name, kind, shape) = form;
        let keyword = |key: &str| {
            call.arguments
                .keywords
                .iter()
                .find(|keyword| keyword.arg.as_ref().is_some_and(|arg| arg.as_str() == key))
                .map(|keyword| &keyword.value)
        };
        let positional = |index: usize| call.arguments.args.get(index);
        let (reason, condition) = match shape {
            Shape::ReasonFirst => (keyword("reason").or_else(|| positional(0)), None),
            Shape::ConditionFirst => (
                keyword("reason").or_else(|| positional(1)),
                keyword("condition").or_else(|| positional(0)),
            ),
            Shape::KeywordReason => (
                keyword("reason"),
                keyword("condition").or_else(|| positional(0)),
            ),
            Shape::Bare => (keyword("reason"), None),
        };
        let strict = keyword("strict")
            .is_some_and(|value| matches!(value, Expr::BooleanLiteral(literal) if literal.value));
        let environment = self.conditional > 0
            || condition.is_some_and(|condition| !is_literal(condition))
            || (kind == Kind::Xfail && strict);
        self.found.push(Use {
            offset: call.start(),
            form: name,
            kind,
            reason: reason.map_or(Reason::Missing, |value| match value {
                Expr::StringLiteral(text) => Reason::Text(text.value.to_str().to_owned()),
                _ => Reason::Dynamic,
            }),
            environment,
        });
    }
}

fn is_literal(expression: &Expr) -> bool {
    matches!(
        expression,
        Expr::BooleanLiteral(_) | Expr::NumberLiteral(_) | Expr::NoneLiteral(_)
    )
}

impl<'a> Visitor<'a> for Collector<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::If(node) => {
                self.visit_expr(&node.test);
                self.conditional += 1;
                self.visit_body(&node.body);
                for clause in &node.elif_else_clauses {
                    if let Some(test) = &clause.test {
                        self.visit_expr(test);
                    }
                    self.visit_body(&clause.body);
                }
                self.conditional -= 1;
            }
            Stmt::Try(node) => {
                self.visit_body(&node.body);
                self.conditional += 1;
                for handler in &node.handlers {
                    self.visit_except_handler(handler);
                }
                self.conditional -= 1;
                self.visit_body(&node.orelse);
                self.visit_body(&node.finalbody);
            }
            // A function body runs later, outside any condition around its definition.
            Stmt::FunctionDef(node) => {
                for decorator in &node.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                let conditional = std::mem::take(&mut self.conditional);
                self.visit_body(&node.body);
                self.conditional = conditional;
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Call(call) => {
                if let Some(form) = self.form(&call.func) {
                    self.record_call(call, form);
                    for argument in &call.arguments.args {
                        self.visit_expr(argument);
                    }
                    for keyword in &call.arguments.keywords {
                        self.visit_expr(&keyword.value);
                    }
                    return;
                }
                visitor::walk_expr(self, expression);
            }
            Expr::Attribute(_) | Expr::Name(_) => {
                // An uncalled marker or decorator applies without any reason.
                if let Some((name, kind, shape)) = self.form(expression)
                    && (name.contains(".mark.") || matches!(shape, Shape::Bare))
                {
                    self.found.push(Use {
                        offset: expression.start(),
                        form: name,
                        kind,
                        reason: Reason::Missing,
                        environment: false,
                    });
                    return;
                }
                visitor::walk_expr(self, expression);
            }
            other => visitor::walk_expr(self, other),
        }
    }
}
