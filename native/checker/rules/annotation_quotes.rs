//! TYP006: `typing/prefer-deferred-annotations`.
use super::{Finding, RuleInput};
use crate::checker::analysis::edits::{add_from_import, fix, python_strip};
use crate::checker::analysis::nodes::parse_eval;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    Arguments, Comprehension, Expr, FStringPartRef, InterpolatedElement, InterpolatedStringElement,
    InterpolatedStringElements, Keyword, ModModule, Parameter, Parameters, Stmt,
};
use ruff_text_size::{Ranged, TextRange, TextSize};
use serde_json::{Value, json};

/// A string constant inside an annotation, as CPython's tree holds it.
struct Quoted {
    range: TextRange,
    value: String,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let mut annotations = Annotations { found: Vec::new() };
    annotations.visit_body(&module.body);
    let mut quoted = Vec::new();
    for annotation in annotations.found {
        collect(annotation, &mut quoted);
    }
    if quoted.is_empty() {
        return Vec::new();
    }
    quoted.sort_by_key(|node| node.range.start());
    let target = input.targets.for_path(&input.source.absolute);
    let policy = input
        .contracts
        .policy_for(&input.source.module, "future_annotations")
        .map(|origin| origin.value);
    let (action, reason) = if target.deferred_annotations() {
        ("unquote", "annotations are deferred on Python 3.14+")
    } else if has_future_import(module) {
        (
            "unquote",
            "the annotations future import already defers them",
        )
    } else if policy.is_some_and(|value| value == "forbid") {
        return Vec::new();
    } else {
        (
            "add-future-import",
            "an annotations future import defers them",
        )
    };
    let repair = rewrite(input, &quoted, action);
    let (line, column) = input.place(quoted[0].range.start());
    let listed: Vec<Value> = quoted
        .iter()
        .map(|node| json!({"line": input.place(node.range.start()).0, "text": node.value}))
        .collect();
    let hint = if action == "unquote" {
        "Remove the quotes."
    } else {
        "Add from __future__ import annotations and remove the quotes; verify runtime annotation consumers."
    };
    vec![input.violation(Finding {
        message: format!("{} quoted annotation(s); {reason}", quoted.len()),
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({
            "action": action,
            "annotations": listed,
            "future_annotations": policy,
            "python": target.to_json(),
            "automatic_rewrite": repair.is_some(),
        }),
        hint: hint.to_owned(),
        fix: repair,
    })]
}

/// Unquote every annotation, adding the future import first when needed.
///
/// Safe only under an existing future import: annotations stay strings at runtime.
fn rewrite(input: &RuleInput<'_>, quoted: &[Quoted], action: &str) -> Option<Value> {
    let parsed = input.parsed;
    let text = &parsed.text;
    let mut edits = Vec::with_capacity(quoted.len() + 1);
    for node in quoted {
        if node.value.contains('\n') || !is_expression(&node.value) {
            return None;
        }
        edits.push(text.replace(node.range, python_strip(&node.value)));
    }
    if action == "add-future-import" {
        edits.insert(0, add_from_import(parsed, "__future__", "annotations"));
        return Some(fix(
            "unsafe",
            "Add from __future__ import annotations and remove the quotes",
            edits,
        ));
    }
    let applicability = if has_future_import(parsed.module()) {
        "safe"
    } else {
        "unsafe"
    };
    Some(fix(
        applicability,
        "Remove the quotes from deferred annotations",
        edits,
    ))
}

fn is_expression(text: &str) -> bool {
    let Some(parsed) = parse_eval(python_strip(text)) else {
        return false;
    };
    // Operators that bind looser than a subscript or | would need parentheses.
    !matches!(
        parsed.expr(),
        Expr::Lambda(_) | Expr::If(_) | Expr::Named(_) | Expr::Yield(_) | Expr::Await(_)
    )
}

fn has_future_import(module: &ModModule) -> bool {
    module.body.iter().any(|statement| {
        matches!(statement, Stmt::ImportFrom(node)
            if node.level == 0
                && node.module.as_ref().is_some_and(|name| name.as_str() == "__future__")
                && node.names.iter().any(|alias| alias.name.as_str() == "annotations"))
    })
}

/// Parameter, return and annotated-assignment annotations anywhere in the file.
struct Annotations<'a> {
    found: Vec<&'a Expr>,
}

impl<'a> Visitor<'a> for Annotations<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => self.found.extend(node.returns.as_deref()),
            Stmt::AnnAssign(node) => self.found.push(&node.annotation),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_parameter(&mut self, parameter: &'a Parameter) {
        self.found.extend(parameter.annotation());
        visitor::walk_parameter(self, parameter);
    }
}

fn collect(node: &Expr, found: &mut Vec<Quoted>) {
    match node {
        Expr::StringLiteral(item) => found.push(Quoted {
            range: item.range(),
            value: item.value.to_str().to_owned(),
        }),
        Expr::Subscript(item) => {
            let name = match item.value.as_ref() {
                Expr::Name(value) => Some(value.id.as_str()),
                Expr::Attribute(value) => Some(value.attr.as_str()),
                _ => None,
            };
            if name == Some("Literal") {
                return;
            }
            if name == Some("Annotated")
                && let Expr::Tuple(index) = item.slice.as_ref()
                && let Some(first) = index.elts.first()
            {
                collect(first, found);
                return;
            }
            collect(&item.value, found);
            collect(&item.slice, found);
        }
        Expr::FString(item) => {
            let mut parts = Vec::new();
            for part in &item.value {
                match part {
                    FStringPartRef::Literal(literal) => {
                        parts.push(Part::Constant(literal.range(), literal.value.to_string()));
                    }
                    FStringPartRef::FString(fstring) => {
                        push_elements(&mut parts, &fstring.elements)
                    }
                }
            }
            emit(parts, found);
        }
        Expr::TString(item) => {
            let mut parts = Vec::new();
            for template in &item.value {
                push_elements(&mut parts, &template.elements);
            }
            emit(parts, found);
        }
        other => {
            let mut children = ExpressionChildren { found };
            visitor::walk_expr(&mut children, other);
        }
    }
}

/// One value of a `JoinedStr` or `TemplateStr`.
enum Part<'a> {
    Constant(TextRange, String),
    Value(&'a InterpolatedElement),
}

fn push_elements<'a>(parts: &mut Vec<Part<'a>>, elements: &'a InterpolatedStringElements) {
    for element in elements {
        match element {
            InterpolatedStringElement::Literal(literal) => {
                parts.push(Part::Constant(literal.range(), literal.value.to_string()));
            }
            InterpolatedStringElement::Interpolation(value) => {
                if let Some(debug) = &value.debug_text {
                    let text = debug.as_str();
                    let start = value.start() + TextSize::from(1);
                    let length = TextSize::try_from(text.len()).expect("sources fit in u32");
                    parts.push(Part::Constant(
                        TextRange::at(start, length),
                        text.to_owned(),
                    ));
                }
                parts.push(Part::Value(value));
            }
        }
    }
}

/// Fold adjacent constants like CPython: a run spans its first to its last piece.
fn emit(parts: Vec<Part<'_>>, found: &mut Vec<Quoted>) {
    let mut run: Option<Quoted> = None;
    for part in parts {
        match part {
            Part::Constant(range, value) => match &mut run {
                Some(current) => {
                    current.range = current.range.cover(range);
                    current.value.push_str(&value);
                }
                None => run = Some(Quoted { range, value }),
            },
            Part::Value(element) => {
                flush(&mut run, found);
                collect(&element.expression, found);
                if let Some(spec) = &element.format_spec {
                    let mut nested = Vec::new();
                    push_elements(&mut nested, &spec.elements);
                    emit(nested, found);
                }
            }
        }
    }
    flush(&mut run, found);
}

fn flush(run: &mut Option<Quoted>, found: &mut Vec<Quoted>) {
    if let Some(current) = run.take()
        && !current.value.is_empty()
    {
        found.push(current);
    }
}

/// The direct children of an expression that are themselves expressions.
struct ExpressionChildren<'b> {
    found: &'b mut Vec<Quoted>,
}

impl<'a> Visitor<'a> for ExpressionChildren<'_> {
    fn visit_expr(&mut self, node: &'a Expr) {
        collect(node, self.found);
    }

    fn visit_comprehension(&mut self, _: &'a Comprehension) {}

    fn visit_keyword(&mut self, _: &'a Keyword) {}

    fn visit_parameters(&mut self, _: &'a Parameters) {}

    fn visit_arguments(&mut self, arguments: &'a Arguments) {
        for argument in &arguments.args {
            collect(argument, self.found);
        }
    }
}
