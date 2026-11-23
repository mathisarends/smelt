//! CPX003: `complexity/excess-positional-parameters`.
use super::{Finding, RuleInput};
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use regex::Regex;
use ruff_python_ast::{
    Decorator, ExceptHandler, Expr, ParameterWithDefault, Stmt, StmtFunctionDef,
};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::sync::LazyLock;

const EXEMPT_DECORATORS: [&str; 2] = ["overload", "override"];

// The separator between the last positional and the first keyword-only parameter.
static BARE_STAR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*,\s*\*\s*,\s*$").expect("the separator pattern is valid"));

struct Signature<'a> {
    node: &'a StmtFunctionDef,
    parameters: Vec<&'a ParameterWithDefault>,
    positional: Vec<&'a str>,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let (limit, origin) = input.contracts.max_positional(&input.source.module);
    let label = origin
        .as_ref()
        .map_or("default", |item| item.label.as_str());
    let contract = origin.as_ref().map(|item| item.contract);
    let mut signatures = Vec::new();
    walk(&input.parsed.module().body, false, &mut signatures);
    let mut found = Vec::new();
    for function in signatures {
        if function.positional.len() <= limit {
            continue;
        }
        let fix = suggestion(input.parsed, &function, limit);
        let excess = &function.positional[limit..];
        let anchor = if limit > 0 {
            format!("after {}", function.positional[limit - 1])
        } else {
            "before the first parameter".to_owned()
        };
        let name = function.node.name.as_str();
        let start = input.parsed.function_start(function.node);
        let (line, column) = input.parsed.text.position(usize::from(start));
        found.push(input.violation(Finding {
            message: format!(
                "{name} accepts {} positional parameters; {label} allows {limit}",
                function.positional.len()
            ),
            line,
            column: column + 1,
            end_line: None,
            end_column: None,
            expected: json!({
                "contract": contract,
                "max_positional_parameters": limit,
                "function": name,
                "positional": function.positional,
                "keyword_only_candidates": excess,
                "automatic_rewrite": false,
                "suggested_rewrite": fix.is_some(),
            }),
            hint: format!(
                "Make {} keyword-only by placing * {anchor}; pass them by keyword at call sites.",
                excess.join(", ")
            ),
            fix,
        }));
    }
    found
}

/// Insert `*` before the first excess parameter; shown, never applied.
fn suggestion(parsed: &ParsedSource, function: &Signature<'_>, limit: usize) -> Option<Value> {
    let arguments = &function.node.parameters;
    if arguments.vararg.is_some() {
        return None;
    }
    let skipped = function.parameters.len() - function.positional.len();
    let index = skipped + limit;
    if index < arguments.posonlyargs.len() {
        return None;
    }
    let first = function.parameters[index];
    let text = &parsed.text;
    let start = usize::from(first.parameter.start());
    let mut edits = vec![text.edit(start, start, "*, ")];
    if let Some(keyword) = arguments.kwonlyargs.first() {
        // The existing bare * moves to the new position.
        let last = function.parameters.last()?;
        let mut end = usize::from(last.parameter.end());
        if let Some(default) = function
            .parameters
            .iter()
            .rev()
            .find_map(|parameter| parameter.default.as_deref())
        {
            end = end.max(usize::from(default.end()));
        }
        let stop = usize::from(keyword.parameter.start());
        if !BARE_STAR.is_match(&text.text[end..stop]) {
            return None;
        }
        edits.push(text.edit(end, stop, ", "));
    }
    Some(json!({
        "applicability": "display",
        "description": format!(
            "Make {} and the parameters after it keyword-only",
            first.parameter.name.as_str()
        ),
        "edits": edits,
    }))
}

fn walk<'a>(body: &'a [Stmt], in_class: bool, found: &mut Vec<Signature<'a>>) {
    for statement in body {
        match statement {
            Stmt::ClassDef(node) => walk(&node.body, true, found),
            Stmt::FunctionDef(node) => {
                found.extend(signature(node, in_class));
                walk(&node.body, false, found);
            }
            Stmt::If(node) => {
                walk(&node.body, in_class, found);
                for clause in &node.elif_else_clauses {
                    walk(&clause.body, in_class, found);
                }
            }
            Stmt::For(node) => {
                walk(&node.body, in_class, found);
                walk(&node.orelse, in_class, found);
            }
            Stmt::While(node) => {
                walk(&node.body, in_class, found);
                walk(&node.orelse, in_class, found);
            }
            Stmt::With(node) => walk(&node.body, in_class, found),
            Stmt::Try(node) => {
                walk(&node.body, in_class, found);
                for handler in &node.handlers {
                    let ExceptHandler::ExceptHandler(handler) = handler;
                    walk(&handler.body, in_class, found);
                }
                walk(&node.orelse, in_class, found);
                walk(&node.finalbody, in_class, found);
            }
            Stmt::Match(node) => {
                for case in &node.cases {
                    walk(&case.body, in_class, found);
                }
            }
            _ => {}
        }
    }
}

fn signature(node: &StmtFunctionDef, in_class: bool) -> Option<Signature<'_>> {
    let name = node.name.as_str();
    if name.starts_with("__") && name.ends_with("__") {
        return None;
    }
    let decorators: Vec<&str> = node
        .decorator_list
        .iter()
        .filter_map(decorator_name)
        .collect();
    if decorators
        .iter()
        .any(|decorator| EXEMPT_DECORATORS.contains(decorator))
    {
        return None;
    }
    let parameters: Vec<&ParameterWithDefault> = node
        .parameters
        .posonlyargs
        .iter()
        .chain(&node.parameters.args)
        .collect();
    let mut positional: Vec<&str> = parameters
        .iter()
        .map(|parameter| parameter.parameter.name.as_str())
        .collect();
    if in_class && !decorators.contains(&"staticmethod") && !positional.is_empty() {
        positional.remove(0);
    }
    Some(Signature {
        node,
        parameters,
        positional,
    })
}

fn decorator_name(decorator: &Decorator) -> Option<&str> {
    let expression = match &decorator.expression {
        Expr::Call(call) => call.func.as_ref(),
        other => other,
    };
    match expression {
        Expr::Name(name) => Some(name.id.as_str()),
        Expr::Attribute(attribute) => Some(attribute.attr.as_str()),
        _ => None,
    }
}
