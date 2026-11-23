//! VIS006: `visibility/redundant-private-delegate`.
use super::{Finding, RuleInput};
use crate::checker::analysis::dump::joined_constants;
use crate::checker::analysis::nodes::walk_statements;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprCall, Stmt, StmtClassDef, StmtFunctionDef};
use serde_json::json;
use std::collections::{HashMap, HashSet};

const NEUTRAL_BASES: [&str; 4] = ["object", "ABC", "Protocol", "Generic"];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let classes: Vec<&StmtClassDef> = walk_statements(module)
        .into_iter()
        .filter_map(|statement| match statement {
            Stmt::ClassDef(node) => Some(node),
            _ => None,
        })
        .collect();
    // Like the Python dict, the last class of a name in walk order wins.
    let local: HashMap<&str, &StmtClassDef> = classes
        .iter()
        .map(|class| (class.name.as_str(), *class))
        .collect();
    let mut names = Names {
        source: input.parsed.text.text.as_str(),
        definitions: HashMap::new(),
        references: HashMap::new(),
    };
    names.visit_body(&module.body);
    let mut found = Vec::new();
    for owner in classes {
        if !is_isolated(owner, &local, &mut HashSet::new()) {
            continue;
        }
        let methods: HashMap<&str, &StmtFunctionDef> = owner
            .body
            .iter()
            .filter_map(|statement| match statement {
                Stmt::FunctionDef(node) => Some((node.name.as_str(), node)),
                _ => None,
            })
            .collect();
        for caller in methods.values() {
            let Some(callee) = forwarded(caller).and_then(|name| methods.get(name)) else {
                continue;
            };
            let name = callee.name.as_str();
            if std::ptr::eq(*callee, *caller)
                || !callee.decorator_list.is_empty()
                || names.definitions.get(name) != Some(&1)
                || names.references.get(name) != Some(&1)
                || !same_parameters(caller, callee)
            {
                continue;
            }
            let (line, column) = input.place(input.parsed.function_start(caller));
            let (delegate_line, _) = input.place(input.parsed.function_start(callee));
            found.push(input.violation(Finding {
                message: format!("{} only forwards to {name}", caller.name),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "class": owner.name.as_str(),
                    "method": caller.name.as_str(),
                    "delegate": name,
                    "delegate_line": delegate_line,
                    "automatic_rewrite": false,
                }),
                hint: format!(
                    "Move the body of {name} into {} and delete {name}.",
                    caller.name
                ),
                fix: None,
            }));
        }
    }
    found
}

/// Defined functions and references. Only private names can be delegates.
struct Names<'s, 'a> {
    source: &'s str,
    definitions: HashMap<&'a str, usize>,
    references: HashMap<String, usize>,
}

impl Names<'_, '_> {
    fn reference(&mut self, name: &str) {
        if name.starts_with('_') {
            *self.references.entry(name.to_owned()).or_insert(0) += 1;
        }
    }
}

impl<'a> Visitor<'a> for Names<'_, 'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if let Stmt::FunctionDef(node) = statement {
            *self.definitions.entry(node.name.as_str()).or_insert(0) += 1;
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Attribute(node) => self.reference(node.attr.as_str()),
            Expr::Name(node) => self.reference(node.id.as_str()),
            Expr::StringLiteral(node) => self.reference(node.value.to_str()),
            Expr::FString(_) | Expr::TString(_) => {
                for constant in joined_constants(self.source, expression) {
                    self.reference(&constant);
                }
            }
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
}

/// No base class outside this module could call a private hook.
fn is_isolated<'a>(
    owner: &'a StmtClassDef,
    local: &HashMap<&str, &'a StmtClassDef>,
    visiting: &mut HashSet<*const StmtClassDef>,
) -> bool {
    if !visiting.insert(std::ptr::from_ref(owner)) {
        return false;
    }
    let isolated = owner.bases().iter().all(|base| {
        let (name, parent) = match base {
            Expr::Subscript(node) => match node.value.as_ref() {
                Expr::Name(value) => (value.id.as_str(), None),
                Expr::Attribute(value) => (value.attr.as_str(), None),
                _ => return false,
            },
            Expr::Name(node) => (node.id.as_str(), local.get(node.id.as_str()).copied()),
            Expr::Attribute(node) => (node.attr.as_str(), None),
            _ => return false,
        };
        NEUTRAL_BASES.contains(&name)
            || parent.is_some_and(|parent| {
                !std::ptr::eq(parent, owner) && is_isolated(parent, local, visiting)
            })
    });
    visiting.remove(&std::ptr::from_ref(owner));
    isolated
}

fn is_dunder(name: &str) -> bool {
    name.starts_with("__") && name.ends_with("__")
}

fn is_docstring(statement: &Stmt) -> bool {
    matches!(statement, Stmt::Expr(node) if node.value.is_string_literal_expr())
}

/// The private method a method body only returns a call of.
fn forwarded(caller: &StmtFunctionDef) -> Option<&str> {
    if is_dunder(caller.name.as_str()) || !caller.decorator_list.is_empty() {
        return None;
    }
    let body = match caller.body.as_slice() {
        [first, rest @ ..] if is_docstring(first) => rest,
        all => all,
    };
    let [Stmt::Return(returned)] = body else {
        return None;
    };
    let Some(Expr::Call(call)) = returned.value.as_deref() else {
        return None;
    };
    let Expr::Attribute(function) = call.func.as_ref() else {
        return None;
    };
    let Expr::Name(owner) = function.value.as_ref() else {
        return None;
    };
    let name = function.attr.as_str();
    (positional(caller).first() == Some(&owner.id.as_str())
        && name.starts_with('_')
        && !is_dunder(name)
        && forwards_all(caller, call))
    .then_some(name)
}

fn positional(function: &StmtFunctionDef) -> Vec<&str> {
    let parameters = &function.parameters;
    parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .map(|item| item.parameter.name.as_str())
        .collect()
}

fn forwards_all(caller: &StmtFunctionDef, call: &ExprCall) -> bool {
    let parameters = &caller.parameters;
    let all = positional(caller);
    let receiving = all.get(1..).unwrap_or_default();
    let vararg = parameters.vararg.as_ref().map(|item| item.name.as_str());
    let kwarg = parameters.kwarg.as_ref().map(|item| item.name.as_str());
    let mut passed: Vec<String> = Vec::new();
    for (index, value) in call.arguments.args.iter().enumerate() {
        match value {
            Expr::Name(node) if receiving.get(index) == Some(&node.id.as_str()) => {
                passed.push(node.id.to_string());
            }
            Expr::Starred(node) => match node.value.as_ref() {
                Expr::Name(name) if vararg == Some(name.id.as_str()) => {
                    passed.push(format!("*{}", name.id));
                }
                _ => return false,
            },
            _ => return false,
        }
    }
    for keyword in &call.arguments.keywords {
        let Expr::Name(value) = &keyword.value else {
            return false;
        };
        match &keyword.arg {
            None if kwarg == Some(value.id.as_str()) => passed.push(format!("**{}", value.id)),
            Some(arg) if arg.as_str() == value.id.as_str() => passed.push(value.id.to_string()),
            _ => return false,
        }
    }
    let mut expected: Vec<String> = receiving.iter().map(|name| (*name).to_owned()).collect();
    expected.extend(
        parameters
            .kwonlyargs
            .iter()
            .map(|item| item.parameter.name.to_string()),
    );
    expected.extend(vararg.map(|name| format!("*{name}")));
    expected.extend(kwarg.map(|name| format!("**{name}")));
    passed.sort_unstable();
    expected.sort_unstable();
    passed == expected
}

fn same_parameters(caller: &StmtFunctionDef, callee: &StmtFunctionDef) -> bool {
    shape(caller) == shape(callee)
}

type Shape<'a> = (
    usize,
    Vec<&'a str>,
    Option<&'a str>,
    Vec<&'a str>,
    Option<&'a str>,
    bool,
);

fn shape(function: &StmtFunctionDef) -> Shape<'_> {
    let parameters = &function.parameters;
    let all = positional(function);
    (
        parameters.posonlyargs.len(),
        all.get(1..).unwrap_or_default().to_vec(),
        parameters.vararg.as_ref().map(|item| item.name.as_str()),
        parameters
            .kwonlyargs
            .iter()
            .map(|item| item.parameter.name.as_str())
            .collect(),
        parameters.kwarg.as_ref().map(|item| item.name.as_str()),
        !all.is_empty(),
    )
}
