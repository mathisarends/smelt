//! IDM002: `idioms/prefer-match-type-dispatch`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{BindingCounts, Coverage};
use crate::checker::analysis::repr::str_tuple_repr;
use crate::checker::analysis::unparse::{unparse, unparse_and};
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{BoolOp, Expr, ExprContext, ModModule, Operator, Stmt, StmtIf};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

struct Head {
    subject: String,
    types: Vec<String>,
    guard: Option<String>,
}

struct Branch {
    line: usize,
    types: Vec<String>,
    guard: Option<String>,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let Some(origin) = input
        .contracts
        .policy_for(&input.source.module, "type_dispatch")
    else {
        return Vec::new();
    };
    if origin.value["style"] != "match" {
        return Vec::new();
    }
    let target = input.targets.for_path(&input.source.absolute);
    if target.below(3, 10) {
        return Vec::new();
    }
    let minimum = origin.value["min_branches"]
        .as_u64()
        .and_then(|value| usize::try_from(value).ok())
        .unwrap_or(3);
    let module = input.parsed.module();
    let checkers = checker_names(module);
    if checkers.is_empty() {
        return Vec::new();
    }
    let mut roots = RootCollector {
        scope: vec!["module"],
        found: Vec::new(),
    };
    roots.visit_body(&module.body);
    let source = input.parsed.text.text.as_str();
    let mut found = Vec::new();
    for (root, scope) in roots.found {
        let Some((subject, branches)) = chain(input, root, &checkers, source) else {
            continue;
        };
        if branches.len() < minimum {
            continue;
        }
        let mut counts: HashMap<String, usize> = HashMap::new();
        let mut edges = Vec::with_capacity(branches.len());
        for branch in &branches {
            let guard = branch.guard.as_deref().unwrap_or("None");
            let key = format!(
                "{scope}:{subject}:{}:{guard}",
                str_tuple_repr(&branch.types)
            );
            let count = counts.entry(key.clone()).or_insert(0);
            edges.push(format!("{key}#{count}"));
            *count += 1;
        }
        let details: Vec<Value> = branches
            .iter()
            .map(
                |branch| json!({"line": branch.line, "types": branch.types, "guard": branch.guard}),
            )
            .collect();
        let (line, column) = input.place(root.start());
        found.push(input.violation(Finding {
            message: format!(
                "consider match {subject}: for {} type-dispatch branches",
                branches.len()
            ),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "contract": origin.contract,
                "type_dispatch": origin.value,
                "subject": subject,
                "scope": scope,
                "dispatch_edges": edges,
                "branches": details,
                "python": target.to_json(),
                "automatic_rewrite": false,
            }),
            hint: format!(
                "Review match {subject}: with class patterns and guards; preserve separate if statements and nested branch conditions."
            ),
            fix: None,
        }));
    }
    found
}

fn chain(
    input: &RuleInput<'_>,
    root: &StmtIf,
    checkers: &HashSet<String>,
    source: &str,
) -> Option<(String, Vec<Branch>)> {
    let first = head(&root.test, checkers, source)?;
    let subject = first.subject.clone();
    let mut branches = vec![Branch {
        line: input.place(root.start()).0,
        types: first.types,
        guard: first.guard,
    }];
    for clause in &root.elif_else_clauses {
        let Some(test) = &clause.test else {
            break;
        };
        let next = head(test, checkers, source)?;
        if next.subject != subject {
            return None;
        }
        branches.push(Branch {
            line: input.place(clause.start()).0,
            types: next.types,
            guard: next.guard,
        });
    }
    Some((subject, branches))
}

/// Every `if` that is not an `elif`, with its dotted enclosing scope.
struct RootCollector<'a> {
    scope: Vec<&'a str>,
    found: Vec<(&'a StmtIf, String)>,
}

impl<'a> Visitor<'a> for RootCollector<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let name = match statement {
            Stmt::FunctionDef(node) => Some(node.name.as_str()),
            Stmt::ClassDef(node) => Some(node.name.as_str()),
            Stmt::If(node) => {
                self.found.push((node, self.scope.join(".")));
                None
            }
            _ => None,
        };
        if let Some(name) = name {
            self.scope.push(name);
        }
        visitor::walk_stmt(self, statement);
        if name.is_some() {
            self.scope.pop();
        }
    }
}

/// Spellings of `isinstance`, rejecting any rebound anywhere in the file.
fn checker_names(module: &ModModule) -> HashSet<String> {
    let bound = BindingCounts::new(module, Coverage::Direct);
    if bound.get("*") > 0 {
        return HashSet::new();
    }
    let mut names = HashSet::new();
    if bound.get("isinstance") == 0 {
        names.insert("isinstance".to_owned());
    }
    let mut builtin_modules = HashSet::new();
    for statement in &module.body {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    if alias.name.as_str() == "builtins" && bound.get(name) == 1 {
                        names.insert(format!("{name}.isinstance"));
                        builtin_modules.insert(name);
                    }
                }
            }
            Stmt::ImportFrom(node)
                if node.level == 0
                    && node.module.as_ref().is_some_and(|name| name == "builtins") =>
            {
                for alias in &node.names {
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    if alias.name.as_str() == "isinstance" && bound.get(name) == 1 {
                        names.insert(name.to_owned());
                    }
                }
            }
            _ => {}
        }
    }
    if !builtin_modules.is_empty() {
        let mut writes = ModuleWrites {
            modules: &builtin_modules,
            inside: false,
            found: false,
        };
        writes.visit_body(&module.body);
        if writes.found {
            return HashSet::new();
        }
    }
    names
}

/// Finds a written attribute or subscript that mentions a builtins module.
struct ModuleWrites<'m> {
    modules: &'m HashSet<&'m str>,
    inside: bool,
    found: bool,
}

impl<'a> Visitor<'a> for ModuleWrites<'_> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if self.inside
            && let Expr::Name(name) = expression
            && self.modules.contains(name.id.as_str())
        {
            self.found = true;
        }
        let written = match expression {
            Expr::Attribute(node) => node.ctx != ExprContext::Load,
            Expr::Subscript(node) => node.ctx != ExprContext::Load,
            _ => false,
        };
        let outer = self.inside;
        self.inside = outer || written;
        visitor::walk_expr(self, expression);
        self.inside = outer;
    }
}

fn head(test: &Expr, checkers: &HashSet<String>, source: &str) -> Option<Head> {
    let values: &[Expr] = match test {
        Expr::BoolOp(item) if item.op == BoolOp::And => &item.values,
        other => std::slice::from_ref(other),
    };
    let Expr::Call(call) = &values[0] else {
        return None;
    };
    let ([Expr::Name(subject), types], []) = (&*call.arguments.args, &*call.arguments.keywords)
    else {
        return None;
    };
    if !dotted(&call.func).is_some_and(|name| checkers.contains(&name)) {
        return None;
    }
    let names = type_names(types)?;
    let subject = subject.id.as_str();
    let mut walrus = WalrusFinder {
        subject,
        found: false,
    };
    walrus.visit_expr(test);
    if walrus.found {
        return None;
    }
    let guard = match &values[1..] {
        [] => None,
        [single] => Some(unparse(source, single)),
        guards => Some(unparse_and(source, guards)),
    };
    Some(Head {
        subject: subject.to_owned(),
        types: names,
        guard,
    })
}

struct WalrusFinder<'s> {
    subject: &'s str,
    found: bool,
}

impl<'a> Visitor<'a> for WalrusFinder<'_> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Named(node) = expression
            && matches!(node.target.as_ref(), Expr::Name(name) if name.id.as_str() == self.subject)
        {
            self.found = true;
        }
        visitor::walk_expr(self, expression);
    }
}

fn dotted(expression: &Expr) -> Option<String> {
    match expression {
        Expr::Name(node) => Some(node.id.to_string()),
        Expr::Attribute(node) => dotted(&node.value).map(|owner| format!("{owner}.{}", node.attr)),
        _ => None,
    }
}

fn type_names(node: &Expr) -> Option<Vec<String>> {
    match node {
        Expr::Name(item) => Some(vec![item.id.to_string()]),
        Expr::Attribute(item) => {
            let parent = type_names(&item.value)?;
            let [single] = parent.as_slice() else {
                return None;
            };
            Some(vec![format!("{single}.{}", item.attr)])
        }
        Expr::Tuple(item) if !item.elts.is_empty() => {
            let mut names = Vec::new();
            for element in &item.elts {
                names.extend(type_names(element)?);
            }
            Some(names)
        }
        Expr::BinOp(item) if item.op == Operator::BitOr => {
            let mut first = type_names(&item.left)?;
            first.extend(type_names(&item.right)?);
            Some(first)
        }
        _ => None,
    }
}
