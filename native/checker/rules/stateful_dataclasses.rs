//! MOD005: `modeling/dataclass-field-mutation`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::dotted_tail;
use crate::checker::analysis::nodes::{all_classes, parse_eval};
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Keyword, Stmt, StmtClassDef, StmtFunctionDef};
use serde_json::json;
use std::collections::{BTreeSet, HashSet};

const MUTABLE_CONTAINERS: [&str; 11] = [
    "list",
    "dict",
    "set",
    "bytearray",
    "defaultdict",
    "deque",
    "Counter",
    "OrderedDict",
    "MutableMapping",
    "MutableSequence",
    "MutableSet",
];
const MUTATING_METHODS: [&str; 19] = [
    "add",
    "append",
    "appendleft",
    "clear",
    "difference_update",
    "discard",
    "extend",
    "extendleft",
    "insert",
    "intersection_update",
    "pop",
    "popitem",
    "popleft",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "symmetric_difference_update",
    "update",
];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let mut found = Vec::new();
    for owner in all_classes(input.parsed.module()) {
        if !is_mutable_dataclass(owner) {
            continue;
        }
        let containers = container_fields(owner);
        let mut methods: Vec<&str> = Vec::new();
        let mut fields: BTreeSet<&str> = BTreeSet::new();
        for statement in &owner.body {
            let Stmt::FunctionDef(method) = statement else {
                continue;
            };
            if method.name.as_str() == "__post_init__" {
                continue;
            }
            let Some(receiver) = receiver(method) else {
                continue;
            };
            let mut mutations = Mutations {
                receiver,
                containers: &containers,
                found: HashSet::new(),
            };
            mutations.visit_stmt(statement);
            if !mutations.found.is_empty() {
                methods.push(method.name.as_str());
                fields.extend(mutations.found);
            }
        }
        if methods.is_empty() {
            continue;
        }
        let name = owner.name.as_str();
        let (line, column) = input.place(input.parsed.class_start(owner));
        found.push(input.violation(Finding {
            message: format!(
                "Dataclass {name} mutates its own fields after construction in {}",
                methods.join(", ")
            ),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "class": name,
                "methods": methods,
                "fields": fields,
                "automatic_rewrite": false,
            }),
            hint: format!(
                "Build the values before constructing {name} and make it frozen, or keep the accumulation in a plain class with private attributes."
            ),
            fix: None,
        }));
    }
    found
}

fn is_mutable_dataclass(owner: &StmtClassDef) -> bool {
    for decorator in &owner.decorator_list {
        let (target, keywords): (&Expr, &[Keyword]) = match &decorator.expression {
            Expr::Call(call) => (&call.func, &call.arguments.keywords),
            other => (other, &[]),
        };
        if dotted_tail(target) != "dataclass" {
            continue;
        }
        let frozen = keywords.iter().find(|item| {
            item.arg
                .as_ref()
                .is_some_and(|arg| arg.as_str() == "frozen")
        });
        // An unknown frozen expression is not evidence of a mutable dataclass.
        return match frozen.map(|item| &item.value) {
            None => true,
            Some(Expr::BooleanLiteral(value)) => !value.value,
            Some(_) => false,
        };
    }
    false
}

fn container_fields(owner: &StmtClassDef) -> HashSet<&str> {
    let mut names = HashSet::new();
    for statement in &owner.body {
        let Stmt::AnnAssign(node) = statement else {
            continue;
        };
        let Expr::Name(target) = node.target.as_ref() else {
            continue;
        };
        if is_container(&node.annotation) || is_container_factory(node.value.as_deref()) {
            names.insert(target.id.as_str());
        }
    }
    names
}

fn is_container(annotation: &Expr) -> bool {
    if let Expr::StringLiteral(text) = annotation {
        return parse_eval(text.value.to_str())
            .is_some_and(|parsed| names_container(parsed.expr()));
    }
    names_container(annotation)
}

fn names_container(annotation: &Expr) -> bool {
    let annotation = match annotation {
        Expr::Subscript(node) => node.value.as_ref(),
        other => other,
    };
    MUTABLE_CONTAINERS.contains(&dotted_tail(annotation).as_str())
}

fn is_container_factory(value: Option<&Expr>) -> bool {
    let Some(Expr::Call(call)) = value else {
        return false;
    };
    call.arguments.keywords.iter().any(|item| {
        item.arg
            .as_ref()
            .is_some_and(|arg| arg.as_str() == "default_factory")
            && MUTABLE_CONTAINERS.contains(&dotted_tail(&item.value).as_str())
    })
}

fn receiver(method: &StmtFunctionDef) -> Option<&str> {
    let binds_no_instance = method.decorator_list.iter().any(|decorator| {
        matches!(
            dotted_tail(&decorator.expression).as_str(),
            "staticmethod" | "classmethod"
        )
    });
    if binds_no_instance {
        return None;
    }
    let parameters = &method.parameters;
    parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .next()
        .map(|parameter| parameter.parameter.name.as_str())
}

struct Mutations<'a, 'b> {
    receiver: &'b str,
    containers: &'b HashSet<&'a str>,
    found: HashSet<&'a str>,
}

impl<'a> Mutations<'a, '_> {
    fn written(&mut self, target: &'a Expr) {
        match target {
            Expr::Tuple(node) => node.elts.iter().for_each(|item| self.written(item)),
            Expr::List(node) => node.elts.iter().for_each(|item| self.written(item)),
            Expr::Starred(node) => self.written(&node.value),
            other => {
                let mut target = other;
                while let Expr::Subscript(node) = target {
                    target = &node.value;
                }
                if let Some(name) = self_attribute(target, self.receiver) {
                    self.found.insert(name);
                }
            }
        }
    }
}

impl<'a> Visitor<'a> for Mutations<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::Assign(node) => node.targets.iter().for_each(|item| self.written(item)),
            Stmt::Delete(node) => node.targets.iter().for_each(|item| self.written(item)),
            Stmt::AugAssign(node) => self.written(&node.target),
            Stmt::AnnAssign(node) => self.written(&node.target),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Call(call) = expression {
            match call.func.as_ref() {
                Expr::Attribute(function) => {
                    if let Some(field) = self_attribute(&function.value, self.receiver)
                        && self.containers.contains(field)
                        && MUTATING_METHODS.contains(&function.attr.as_str())
                    {
                        self.found.insert(field);
                    }
                }
                Expr::Name(function) if function.id.as_str() == "setattr" => {
                    if let Some(Expr::Name(first)) = call.arguments.args.first()
                        && first.id.as_str() == self.receiver
                    {
                        self.found.insert("<setattr>");
                    }
                }
                _ => {}
            }
        }
        visitor::walk_expr(self, expression);
    }
}

fn self_attribute<'a>(node: &'a Expr, receiver: &str) -> Option<&'a str> {
    let Expr::Attribute(attribute) = node else {
        return None;
    };
    matches!(attribute.value.as_ref(), Expr::Name(name) if name.id.as_str() == receiver)
        .then_some(attribute.attr.as_str())
}
