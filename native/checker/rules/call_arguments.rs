//! CPX002: `complexity/inline-call-argument`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::unshadowed_bindings;
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprCall, Stmt};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::HashSet;

const AGGREGATES: [&str; 6] = ["all", "any", "max", "min", "sorted", "sum"];
const DEFAULT_KINDS: [&str; 3] = ["await", "comprehension", "aggregate"];
const CALLEE_PREVIEW: usize = 60;

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let kinds = argument_kinds(input);
    let module = input.parsed.module();
    let source = input.parsed.text.text.as_str();
    let mut found = Vec::new();
    if kinds.contains("await") {
        let mut awaited = AwaitedArguments {
            conditional: false,
            found: Vec::new(),
        };
        awaited.visit_body(&module.body);
        for argument in awaited.found {
            found.push(awaited_finding(input, &argument));
        }
    }
    if kinds.contains("comprehension") || kinds.contains("aggregate") {
        let aggregates: HashSet<&str> = if kinds.contains("aggregate") {
            let bindings = unshadowed_bindings(source, module, &AGGREGATES);
            AGGREGATES
                .into_iter()
                .filter(|name| {
                    bindings.get(*name).map(String::as_str) == Some(&format!("builtins.{name}"))
                })
                .collect()
        } else {
            HashSet::new()
        };
        let mut arguments = ComprehensionArguments {
            aggregates: &aggregates,
            found: Vec::new(),
        };
        arguments.visit_body(&module.body);
        for argument in arguments.found {
            if aggregate_name(argument.expression).is_some() || kinds.contains("comprehension") {
                found.push(transformation_finding(input, &argument));
            }
        }
    }
    found
}

fn argument_kinds<'a>(input: &RuleInput<'a>) -> HashSet<&'a str> {
    input
        .contracts
        .policy_for(&input.source.module, "call_argument_kinds")
        .and_then(|origin| origin.value.as_array())
        .filter(|kinds| !kinds.is_empty())
        .map_or_else(
            || DEFAULT_KINDS.into_iter().collect(),
            |kinds| kinds.iter().filter_map(Value::as_str).collect(),
        )
}

/// One direct call argument, with its keyword if it has one.
struct Argument<'a> {
    call: &'a ExprCall,
    expression: &'a Expr,
    keyword: Option<&'a str>,
}

fn callee(source: &str, call: &ExprCall) -> String {
    let text = unparse(source, &call.func);
    if text.chars().count() > CALLEE_PREVIEW {
        let kept: String = text.chars().take(CALLEE_PREVIEW - 3).collect();
        return format!("{kept}...");
    }
    text
}

fn awaited_finding(input: &RuleInput<'_>, argument: &Argument<'_>) -> Violation {
    let source = input.parsed.text.text.as_str();
    let name = callee(source, argument.call);
    let keyword = argument.keyword;
    let hint = keyword.map_or_else(
        || {
            "Assign the awaited result to a named local variable before the call and pass that variable."
                .to_owned()
        },
        |variable| {
            format!(
                "Assign `{variable} = {}` before the call and pass `{variable}={variable}`.",
                unparse(source, argument.expression)
            )
        },
    );
    let Expr::Await(awaited) = argument.expression else {
        unreachable!("awaited arguments hold await expressions");
    };
    let (line, column) = input.place(awaited.start());
    let (end_line, end_column) = input.place(awaited.end());
    input.violation(Finding {
        message: format!("name the awaited result before passing it to {name}()"),
        line,
        column,
        end_line: Some(end_line),
        end_column: Some(end_column),
        expected: json!({
            "kind": "await",
            "call": name,
            "argument": keyword,
            "awaited": unparse(source, &awaited.value),
            "suggested_variable": keyword,
            "automatic_rewrite": false,
        }),
        hint,
        fix: None,
    })
}

fn aggregate_name(expression: &Expr) -> Option<&str> {
    let Expr::Call(call) = expression else {
        return None;
    };
    match call.func.as_ref() {
        Expr::Name(name) => Some(name.id.as_str()),
        _ => None,
    }
}

fn comprehension_kind(expression: &Expr) -> &'static str {
    match expression {
        Expr::ListComp(_) => "ListComp",
        Expr::SetComp(_) => "SetComp",
        Expr::DictComp(_) => "DictComp",
        _ => "GeneratorExp",
    }
}

fn transformation_finding(input: &RuleInput<'_>, argument: &Argument<'_>) -> Violation {
    let expression = argument.expression;
    let keyword = argument.keyword;
    let name = callee(input.parsed.text.text.as_str(), argument.call);
    let aggregate = aggregate_name(expression);
    let subject = aggregate.map_or_else(
        || "the comprehension".to_owned(),
        |aggregate| format!("the {aggregate}() result"),
    );
    let hint = keyword.map_or_else(
        || {
            format!(
                "Assign {subject} to a named local variable before the call and pass that variable; preserve evaluation order."
            )
        },
        |variable| {
            format!(
                "Assign {subject} to `{variable}` before the call and pass `{variable}={variable}`; preserve evaluation order."
            )
        },
    );
    let (line, column) = input.place(expression.start());
    let (end_line, end_column) = input.place(expression.end());
    input.violation(Finding {
        message: format!("Name the collection transformation before passing it to {name}()"),
        line,
        column,
        end_line: Some(end_line),
        end_column: Some(end_column),
        expected: json!({
            "kind": if aggregate.is_some() { "aggregate" } else { "comprehension" },
            "call": name,
            "argument": keyword,
            "comprehension_kind": comprehension_kind(expression),
            "aggregate": aggregate,
            "suggested_variable": keyword,
            "automatic_rewrite": false,
        }),
        hint,
        fix: None,
    })
}

fn is_conditional_scope(expression: &Expr) -> bool {
    matches!(
        expression,
        Expr::If(_)
            | Expr::BoolOp(_)
            | Expr::Lambda(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::Generator(_)
    )
}

struct AwaitedArguments<'a> {
    conditional: bool,
    found: Vec<Argument<'a>>,
}

impl<'a> Visitor<'a> for AwaitedArguments<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let outer = std::mem::replace(&mut self.conditional, false);
        if let Stmt::While(node) = statement {
            self.conditional = true;
            self.visit_expr(&node.test);
            self.conditional = false;
            self.visit_body(&node.body);
            self.visit_body(&node.orelse);
        } else {
            visitor::walk_stmt(self, statement);
        }
        self.conditional = outer;
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Call(call) = expression
            && !self.conditional
        {
            for argument in &call.arguments.args {
                if argument.is_await_expr() {
                    self.found.push(Argument {
                        call,
                        expression: argument,
                        keyword: None,
                    });
                }
            }
            for keyword in &call.arguments.keywords {
                if let Some(name) = &keyword.arg
                    && keyword.value.is_await_expr()
                {
                    self.found.push(Argument {
                        call,
                        expression: &keyword.value,
                        keyword: Some(name.as_str()),
                    });
                }
            }
        }
        let outer = self.conditional;
        self.conditional |= is_conditional_scope(expression);
        visitor::walk_expr(self, expression);
        self.conditional = outer;
    }
}

struct ComprehensionArguments<'a, 'b> {
    aggregates: &'b HashSet<&'b str>,
    found: Vec<Argument<'a>>,
}

impl ComprehensionArguments<'_, '_> {
    fn is_extractable(&self, node: &Expr) -> bool {
        matches!(
            node,
            Expr::ListComp(_) | Expr::SetComp(_) | Expr::DictComp(_)
        ) || self.is_aggregate_reduction(node)
    }

    fn is_aggregate_reduction(&self, node: &Expr) -> bool {
        let Expr::Call(call) = node else {
            return false;
        };
        let Expr::Name(function) = call.func.as_ref() else {
            return false;
        };
        if !self.aggregates.contains(function.id.as_str()) {
            return false;
        }
        let Some(Expr::Generator(generator)) = call.arguments.args.first() else {
            return false;
        };
        !call.arguments.args.iter().any(Expr::is_starred_expr)
            && !call
                .arguments
                .keywords
                .iter()
                .any(|keyword| keyword.arg.is_none())
            && !generator.generators.iter().any(|item| item.is_async)
    }
}

impl<'a> Visitor<'a> for ComprehensionArguments<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::While(node) => {
                self.visit_body(&node.body);
                self.visit_body(&node.orelse);
            }
            Stmt::Assert(node) => self.visit_expr(&node.test),
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if is_conditional_scope(expression) {
            return;
        }
        if let Expr::Compare(node) = expression
            && node.ops.len() > 1
        {
            return;
        }
        if let Expr::Call(call) = expression {
            for argument in &call.arguments.args {
                if self.is_extractable(argument) {
                    self.found.push(Argument {
                        call,
                        expression: argument,
                        keyword: None,
                    });
                }
            }
            for keyword in &call.arguments.keywords {
                if let Some(name) = &keyword.arg
                    && self.is_extractable(&keyword.value)
                {
                    self.found.push(Argument {
                        call,
                        expression: &keyword.value,
                        keyword: Some(name.as_str()),
                    });
                }
            }
        }
        visitor::walk_expr(self, expression);
    }
}
