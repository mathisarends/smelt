//! IDM001: `idioms/bundled-instance-assignment`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{Names, import_bindings};
use crate::checker::analysis::edits::fix;
use crate::checker::analysis::nodes::{is_constant, walk_statements};
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt, StmtAssign, StmtClassDef, StmtFunctionDef};
use ruff_text_size::Ranged;
use serde_json::{Value, json};

const SKIPPED_METHODS: [&str; 3] = ["__new__", "__init_subclass__", "__class_getitem__"];
const RECEIVERLESS: [&str; 4] = [
    "staticmethod",
    "classmethod",
    "builtins.staticmethod",
    "builtins.classmethod",
];
const HINT: &str =
    "Write each field separately, preserving the original evaluation order and unpacking behavior.";

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let bindings = import_bindings(module, &input.source.module, input.source.is_package);
    let mut found = Vec::new();
    for statement in walk_statements(module) {
        let Stmt::ClassDef(owner) = statement else {
            continue;
        };
        for member in &owner.body {
            let Stmt::FunctionDef(method) = member else {
                continue;
            };
            let Some(receiver) = receiver(method, &bindings) else {
                continue;
            };
            let mut assignments = Assignments { found: Vec::new() };
            assignments.visit_body(&method.body);
            for assignment in assignments.found {
                let mut targets = Vec::new();
                for root in &assignment.targets {
                    attributes(root, receiver, &mut targets);
                }
                if targets.len() > 1 {
                    found.push(finding(
                        input,
                        owner,
                        method,
                        assignment,
                        (&targets, receiver),
                    ));
                }
            }
        }
    }
    found
}

fn finding(
    input: &RuleInput<'_>,
    owner: &StmtClassDef,
    method: &StmtFunctionDef,
    assignment: &StmtAssign,
    (targets, receiver): (&[&Expr], &str),
) -> Violation {
    let source = input.parsed.text.text.as_str();
    let repair = rewrite(input, assignment, receiver);
    let (line, column) = input.place(assignment.start());
    let (end_line, end_column) = input.place(assignment.end());
    let attributes: Vec<String> = targets
        .iter()
        .map(|target| unparse(source, target))
        .collect();
    input.violation(Finding {
        message: format!(
            "assignment writes {} instance attributes; use separate statements",
            targets.len()
        ),
        line,
        column,
        end_line: Some(end_line),
        end_column: Some(end_column),
        expected: json!({
            "class": owner.name.as_str(),
            "method": method.name.as_str(),
            "attributes": attributes,
            "automatic_split": repair.is_some(),
        }),
        hint: HINT.to_owned(),
        fix: repair,
    })
}

/// The first positional parameter of an ordinary instance method.
fn receiver<'a>(method: &'a StmtFunctionDef, bindings: &Names<'_>) -> Option<&'a str> {
    if SKIPPED_METHODS.contains(&method.name.as_str()) {
        return None;
    }
    let parameters = &method.parameters;
    let first = parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .next()?;
    let excluded = method.decorator_list.iter().any(|decorator| {
        qualified(&decorator.expression, bindings)
            .is_some_and(|name| RECEIVERLESS.contains(&name.as_str()))
    });
    (!excluded).then(|| first.parameter.name.as_str())
}

fn qualified(node: &Expr, bindings: &Names<'_>) -> Option<String> {
    match node {
        Expr::Name(item) => Some(
            bindings
                .get(item.id.as_str())
                .cloned()
                .unwrap_or_else(|| item.id.to_string()),
        ),
        Expr::Attribute(item) => {
            let parent = qualified(&item.value, bindings)?;
            (!parent.is_empty()).then(|| format!("{parent}.{}", item.attr))
        }
        _ => None,
    }
}

fn attributes<'a>(target: &'a Expr, receiver: &str, found: &mut Vec<&'a Expr>) {
    match target {
        Expr::Attribute(_) if is_field(target, receiver) => found.push(target),
        Expr::Tuple(item) => {
            for element in &item.elts {
                attributes(element, receiver, found);
            }
        }
        Expr::List(item) => {
            for element in &item.elts {
                attributes(element, receiver, found);
            }
        }
        Expr::Starred(item) => attributes(&item.value, receiver, found),
        _ => {}
    }
}

/// Propose one assignment per field for simple values and direct targets.
///
/// Splitting can repeat a lookup in a chain or start writing before all the
/// unpacked values have been evaluated.
fn rewrite(input: &RuleInput<'_>, assignment: &StmtAssign, receiver: &str) -> Option<Value> {
    let text = &input.parsed.text;
    let pairs = pairs(assignment, receiver)?;
    if !text.stands_alone(assignment.range()) || text.segment(assignment.range()).contains('#') {
        return None;
    }
    let start = usize::from(assignment.start());
    let indentation = &text.text[text.line_start(text.line_at(start))..start];
    let lines: Vec<String> = pairs
        .iter()
        .map(|(target, value)| {
            format!(
                "{} = {}",
                text.segment(target.range()),
                text.segment(value.range())
            )
        })
        .collect();
    Some(fix(
        "unsafe",
        "Assign each instance attribute in its own statement",
        vec![text.replace(assignment.range(), &lines.join(&format!("\n{indentation}")))],
    ))
}

/// The (field, value) writes in order for supported assignment shapes.
fn pairs<'a>(assignment: &'a StmtAssign, receiver: &str) -> Option<Vec<(&'a Expr, &'a Expr)>> {
    let value = assignment.value.as_ref();
    if assignment.targets.len() > 1 {
        if !is_simple(value)
            || !assignment
                .targets
                .iter()
                .all(|target| is_field(target, receiver))
        {
            return None;
        }
        return Some(
            assignment
                .targets
                .iter()
                .map(|target| (target, value))
                .collect(),
        );
    }
    let targets = sequence(&assignment.targets[0])?;
    let values = sequence(value)?;
    if targets.len() != values.len()
        || !targets.iter().all(|target| is_field(target, receiver))
        || !values.iter().all(is_simple)
    {
        return None;
    }
    Some(targets.iter().zip(values).collect())
}

fn sequence(node: &Expr) -> Option<&[Expr]> {
    match node {
        Expr::Tuple(item) => Some(&item.elts),
        Expr::List(item) => Some(&item.elts),
        _ => None,
    }
}

fn is_field(target: &Expr, receiver: &str) -> bool {
    matches!(target, Expr::Attribute(item)
        if matches!(item.value.as_ref(), Expr::Name(owner) if owner.id.as_str() == receiver))
}

fn is_simple(value: &Expr) -> bool {
    value.is_name_expr() || is_constant(value)
}

/// Assignments of a method body, outside nested functions and classes.
struct Assignments<'a> {
    found: Vec<&'a StmtAssign>,
}

impl<'a> Visitor<'a> for Assignments<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => {}
            Stmt::Assign(node) => self.found.push(node),
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, _: &'a Expr) {}
}
