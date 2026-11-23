//! TYP003: `typing/implicit-optional-default`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{Names, import_bindings};
use crate::checker::analysis::edits::fix;
use crate::checker::analysis::nodes::{parse_eval, subscript_parts};
use crate::checker::analysis::repr::str_repr;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    BoolOp, Comprehension, ExceptHandler, Expr, ExprBoolOp, ExprContext, FStringPartRef,
    InterpolatedStringElement, InterpolatedStringElements, ModModule, Operator, Pattern, Stmt,
    StmtFunctionDef, UnaryOp,
};
use ruff_text_size::{Ranged, TextRange};
use serde_json::{Value, json};
use std::collections::HashSet;

const OPTIONALS: [&str; 2] = ["typing.Optional", "typing_extensions.Optional"];
const UNIONS: [&str; 2] = ["typing.Union", "typing_extensions.Union"];
const ANNOTATED: [&str; 2] = ["typing.Annotated", "typing_extensions.Annotated"];
const HINT: &str = "Use an explicit is not None conditional if only None should select the fallback; review intentional falsy defaults.";

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let bindings = import_bindings(module, &input.source.module, input.source.is_package);
    let mut scopes = FunctionScopes {
        names: Vec::new(),
        found: Vec::new(),
    };
    scopes.visit_body(&module.body);
    let mut found = Vec::new();
    for (function, scope) in scopes.found {
        for (node, parameter) in candidates(function, &bindings) {
            let (line, column) = input.place(node.start());
            let (end_line, end_column) = input.place(node.end());
            found.push(input.violation(Finding {
                message: format!(
                    "optional parameter {} uses truthiness for its default",
                    str_repr(parameter)
                ),
                line,
                column,
                end_line: Some(end_line),
                end_column: Some(end_column),
                expected: json!({
                    "parameter": parameter,
                    "scope": scope,
                    "fix_safety": "unsafe",
                }),
                hint: HINT.to_owned(),
                fix: rewrite(input, module, node, parameter),
            }));
        }
    }
    found
}

fn candidates<'a>(
    function: &'a StmtFunctionDef,
    bindings: &Names<'_>,
) -> Vec<(&'a ExprBoolOp, &'a str)> {
    let mut optional: HashSet<&str> = function
        .parameters
        .iter_non_variadic_params()
        .filter(|item| matches!(item.default.as_deref(), Some(Expr::NoneLiteral(_))))
        .filter(|item| {
            item.parameter
                .annotation()
                .is_some_and(|annotation| is_optional(annotation, bindings))
        })
        .map(|item| item.parameter.name.as_str())
        .collect();
    if optional.is_empty() {
        return Vec::new();
    }
    let mut bound = BoundNames {
        names: HashSet::new(),
    };
    bound.visit_body(&function.body);
    optional.retain(|name| !bound.names.contains(name));
    if optional.is_empty() {
        return Vec::new();
    }
    let mut values = ValueNodes {
        optional: &optional,
        found: Vec::new(),
    };
    values.visit_body(&function.body);
    values.found
}

fn is_optional(node: &Expr, bindings: &Names<'_>) -> bool {
    match node {
        Expr::StringLiteral(item) => parse_eval(item.value.to_str())
            .is_some_and(|parsed| is_optional(parsed.expr(), bindings)),
        Expr::BinOp(item) if item.op == Operator::BitOr => [&item.left, &item.right]
            .into_iter()
            .any(|part| part.is_none_literal_expr() || is_optional(part, bindings)),
        Expr::Subscript(item) => {
            let name = qualified(&item.value, bindings);
            let parts = subscript_parts(&item.slice);
            if ANNOTATED.contains(&name.as_str()) {
                return parts
                    .first()
                    .is_some_and(|first| is_optional(first, bindings));
            }
            OPTIONALS.contains(&name.as_str())
                || (UNIONS.contains(&name.as_str()) && parts.iter().any(Expr::is_none_literal_expr))
        }
        _ => false,
    }
}

fn qualified(node: &Expr, bindings: &Names<'_>) -> String {
    match node {
        Expr::Name(item) => bindings
            .get(item.id.as_str())
            .cloned()
            .unwrap_or_else(|| item.id.to_string()),
        Expr::Attribute(item) => format!("{}.{}", qualified(&item.value, bindings), item.attr),
        _ => String::new(),
    }
}

/// Unsafe: falsy values stop selecting the fallback and `__bool__` no longer runs.
fn rewrite(
    input: &RuleInput<'_>,
    module: &ModModule,
    node: &ExprBoolOp,
    parameter: &str,
) -> Option<Value> {
    let parsed = input.parsed;
    let text = &parsed.text;
    let segment = text.segment(node.range());
    // Comments or continuations inside the expression need a human edit.
    if segment.contains('#') || segment.contains('\\') {
        return None;
    }
    let mut stored = StoredText {
        target: node.range(),
        found: false,
    };
    stored.visit_body(&module.body);
    if stored.found {
        return None;
    }
    let fallback = text.segment(parsed.comprehension_range(&node.values[1]));
    let replacement = format!("({parameter} if {parameter} is not None else ({fallback}))");
    Some(fix(
        "unsafe",
        &format!(
            "Select the fallback only when {} is None",
            str_repr(parameter)
        ),
        vec![text.replace(node.range(), &replacement)],
    ))
}

/// Finds a target inside an expression whose source text is part of the tree:
/// a debug f-string field or a template interpolation. Rewriting it would change
/// more than the expression, so CPython's comparison rejects the fix.
struct StoredText {
    target: TextRange,
    found: bool,
}

impl StoredText {
    fn elements(&mut self, elements: &InterpolatedStringElements, template: bool) {
        for element in elements {
            let InterpolatedStringElement::Interpolation(value) = element else {
                continue;
            };
            if (template || value.debug_text.is_some())
                && value.expression.range().contains_range(self.target)
            {
                self.found = true;
            }
            Visitor::visit_expr(self, &value.expression);
            if let Some(spec) = &value.format_spec {
                self.elements(&spec.elements, false);
            }
        }
    }
}

impl<'a> Visitor<'a> for StoredText {
    fn visit_expr(&mut self, node: &'a Expr) {
        match node {
            Expr::FString(item) => {
                for part in &item.value {
                    if let FStringPartRef::FString(fstring) = part {
                        self.elements(&fstring.elements, false);
                    }
                }
            }
            Expr::TString(item) => {
                for template in &item.value {
                    self.elements(&template.elements, true);
                }
            }
            other => visitor::walk_expr(self, other),
        }
    }
}

/// Every function with its dotted scope of enclosing functions and classes.
struct FunctionScopes<'a> {
    names: Vec<&'a str>,
    found: Vec<(&'a StmtFunctionDef, String)>,
}

impl<'a> Visitor<'a> for FunctionScopes<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => {
                self.names.push(node.name.as_str());
                self.found.push((node, self.names.join(".")));
                visitor::walk_stmt(self, statement);
                self.names.pop();
            }
            Stmt::ClassDef(node) => {
                self.names.push(node.name.as_str());
                visitor::walk_stmt(self, statement);
                self.names.pop();
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, _: &'a Expr) {}
}

/// Names a function body binds, without entering nested scopes.
struct BoundNames<'a> {
    names: HashSet<&'a str>,
}

impl<'a> Visitor<'a> for BoundNames<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => {
                self.names.insert(node.name.as_str());
                return;
            }
            Stmt::ClassDef(node) => {
                self.names.insert(node.name.as_str());
                return;
            }
            Stmt::Import(node) => {
                for alias in &node.names {
                    self.names.insert(alias_binding(alias));
                }
            }
            Stmt::ImportFrom(node) => {
                for alias in &node.names {
                    self.names.insert(alias_binding(alias));
                }
            }
            Stmt::Global(node) => self
                .names
                .extend(node.names.iter().map(|name| name.as_str())),
            Stmt::Nonlocal(node) => self
                .names
                .extend(node.names.iter().map(|name| name.as_str())),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, node: &'a Expr) {
        match node {
            Expr::Lambda(_) => return,
            Expr::Name(item) if item.ctx != ExprContext::Load => {
                self.names.insert(item.id.as_str());
            }
            _ => {}
        }
        visitor::walk_expr(self, node);
    }

    fn visit_except_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        if let Some(name) = &node.name {
            self.names.insert(name.as_str());
        }
        visitor::walk_except_handler(self, handler);
    }

    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        let name = match pattern {
            Pattern::MatchAs(node) => node.name.as_ref(),
            Pattern::MatchStar(node) => node.name.as_ref(),
            Pattern::MatchMapping(node) => node.rest.as_ref(),
            _ => None,
        };
        if let Some(name) = name {
            self.names.insert(name.as_str());
        }
        visitor::walk_pattern(self, pattern);
    }
}

fn alias_binding(alias: &ruff_python_ast::Alias) -> &str {
    match &alias.asname {
        Some(name) => name.as_str(),
        None => alias.name.split('.').next().unwrap_or_default(),
    }
}

/// `name or fallback` where a value is used, outside boolean tests and scopes.
struct ValueNodes<'a, 'b> {
    optional: &'b HashSet<&'a str>,
    found: Vec<(&'a ExprBoolOp, &'a str)>,
}

impl<'a> Visitor<'a> for ValueNodes<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) | Stmt::Assert(_) => {}
            Stmt::If(node) => {
                self.visit_body(&node.body);
                for clause in &node.elif_else_clauses {
                    self.visit_body(&clause.body);
                }
            }
            Stmt::While(node) => {
                self.visit_body(&node.body);
                self.visit_body(&node.orelse);
            }
            Stmt::Match(node) => {
                self.visit_expr(&node.subject);
                for case in &node.cases {
                    self.visit_body(&case.body);
                }
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, node: &'a Expr) {
        match node {
            Expr::Lambda(_) => {}
            Expr::If(item) => {
                self.visit_expr(&item.body);
                self.visit_expr(&item.orelse);
            }
            Expr::UnaryOp(item) if item.op == UnaryOp::Not => {}
            Expr::BoolOp(item) if item.op == BoolOp::And => {}
            Expr::Call(item) if matches!(item.func.as_ref(), Expr::Name(name) if name.id.as_str() == "bool") =>
                {}
            Expr::BoolOp(item) => {
                if let [Expr::Name(name), _] = item.values.as_slice()
                    && self.optional.contains(name.id.as_str())
                {
                    self.found.push((item, name.id.as_str()));
                }
                visitor::walk_expr(self, node);
            }
            other => visitor::walk_expr(self, other),
        }
    }

    fn visit_comprehension(&mut self, comprehension: &'a Comprehension) {
        self.visit_expr(&comprehension.iter);
    }
}
