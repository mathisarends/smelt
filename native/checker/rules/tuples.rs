//! TYP007: `typing/anonymous-tuple-type`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::dotted_name;
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt, StmtFunctionDef};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::BTreeSet;

const MIN_ELEMENTS: usize = 3;
const TUPLES: [&str; 4] = ["tuple", "Tuple", "typing.Tuple", "typing_extensions.Tuple"];
const EXEMPT_DECORATORS: [&str; 2] = ["override", "overload"];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let source = input.parsed.text.text.as_str();
    let mut annotations = Annotations { found: Vec::new() };
    annotations.visit_body(&module.body);
    let mut tuples = TupleTypes { found: Vec::new() };
    for annotation in annotations.found {
        tuples.visit_expr(annotation);
    }
    let mut found = Vec::new();
    for (node, elements) in tuples.found {
        let spelled: Vec<String> = elements
            .iter()
            .map(|element| unparse(source, element))
            .collect();
        let repeated: BTreeSet<&String> = spelled
            .iter()
            .filter(|element| spelled.iter().filter(|other| other == element).count() > 1)
            .collect();
        if repeated.is_empty() && spelled.len() < MIN_ELEMENTS {
            continue;
        }
        let tuple = unparse(source, node);
        let reason = if repeated.is_empty() {
            "three or more elements"
        } else {
            "repeated element type"
        };
        let (line, column) = input.place(node.start());
        found.push(input.violation(Finding {
            message: format!("{tuple} leaves its fields unnamed ({reason})"),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "tuple": tuple,
                "elements": spelled,
                "repeated": repeated,
                "automatic_rewrite": false,
            }),
            hint: "Name the fields with a NamedTuple or frozen dataclass.".to_owned(),
            fix: None,
        }));
    }
    found
}

fn dictated(function: &StmtFunctionDef) -> bool {
    let name = function.name.as_str();
    if name.starts_with("__") && name.ends_with("__") {
        return true;
    }
    function.decorator_list.iter().any(|decorator| {
        dotted_name(&decorator.expression).is_some_and(|dotted| {
            EXEMPT_DECORATORS.contains(&dotted.rsplit('.').next().unwrap_or(&dotted))
        })
    })
}

struct Annotations<'a> {
    found: Vec<&'a Expr>,
}

impl<'a> Visitor<'a> for Annotations<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) if !dictated(node) => {
                let parameters = &node.parameters;
                let listed = parameters
                    .posonlyargs
                    .iter()
                    .chain(&parameters.args)
                    .chain(&parameters.kwonlyargs)
                    .map(|item| &item.parameter);
                let variadic = parameters.vararg.iter().chain(&parameters.kwarg);
                let declared = listed
                    .chain(variadic.map(Box::as_ref))
                    .filter_map(|parameter| parameter.annotation.as_deref());
                self.found.extend(declared);
                self.found.extend(node.returns.as_deref());
            }
            Stmt::AnnAssign(node) => {
                self.found.push(&node.annotation);
                let alias = dotted_name(&node.annotation)
                    .is_some_and(|name| name == "TypeAlias" || name == "typing.TypeAlias");
                if alias {
                    self.found.extend(node.value.as_deref());
                }
            }
            Stmt::TypeAlias(node) => self.found.push(&node.value),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }
}

/// Fixed-length tuple subscripts with their element nodes.
struct TupleTypes<'a> {
    found: Vec<(&'a Expr, &'a [Expr])>,
}

impl<'a> Visitor<'a> for TupleTypes<'a> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Subscript(node) = expression
            && let Expr::Tuple(slice) = node.slice.as_ref()
            && dotted_name(&node.value).is_some_and(|name| TUPLES.contains(&name.as_str()))
            && !slice.elts.iter().any(Expr::is_ellipsis_literal_expr)
        {
            self.found.push((expression, &slice.elts));
        }
        visitor::walk_expr(self, expression);
    }
}
