//! VIS007: `visibility/excess-private-definitions`.
use super::{Finding, RuleInput};
use crate::checker::analysis::nodes::is_upper;
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::{Ranged, TextSize};
use serde_json::{Value, json};
use std::collections::HashSet;

const DEFAULT_LIMIT: usize = 12;
const HINT: &str = "Review cohesive models or processing steps for a separate module, including their constants and validators; preserve import direction and module visibility. A large cohesive implementation may be intentional.";

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum Kind {
    Class,
    Function,
    TypeAlias,
    Constant,
}

impl Kind {
    fn label(self) -> &'static str {
        match self {
            Self::Class => "class",
            Self::Function => "function",
            Self::TypeAlias => "type_alias",
            Self::Constant => "constant",
        }
    }
}

struct Definition<'a> {
    name: &'a str,
    kind: Kind,
    offset: TextSize,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let limit = input.contracts.threshold(
        &input.source.module,
        "max_private_definitions",
        DEFAULT_LIMIT,
    );
    let (constants, definitions): (Vec<_>, Vec<_>) = declarations(input)
        .into_iter()
        .partition(|item| item.kind == Kind::Constant);
    if definitions.len() <= limit {
        return Vec::new();
    }
    let count = |kind| definitions.iter().filter(|item| item.kind == kind).count();
    let (classes, functions, aliases) = (
        count(Kind::Class),
        count(Kind::Function),
        count(Kind::TypeAlias),
    );
    let (line, column) = input.place(definitions[0].offset);
    let edges: Vec<String> = definitions
        .iter()
        .map(|item| format!("{}:{}", item.kind.label(), item.name))
        .collect();
    vec![input.violation(Finding {
        message: format!(
            "Module {} contains {} private definitions (limit {limit}): {classes} classes, {functions} functions, {aliases} type aliases; review its responsibilities",
            input.source.module,
            definitions.len(),
        ),
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({
            "max_private_definitions": limit,
            "count": definitions.len(),
            "counts": {
                "classes": classes,
                "functions": functions,
                "type_aliases": aliases,
            },
            "definitions": locations(input, &definitions),
            "constant_count": constants.len(),
            "constants": locations(input, &constants),
            "definition_edges": edges,
            "automatic_rewrite": false,
        }),
        hint: HINT.to_owned(),
        fix: None,
    })]
}

fn locations(input: &RuleInput<'_>, definitions: &[Definition<'_>]) -> Vec<Value> {
    definitions
        .iter()
        .map(|item| {
            let (line, column) = input.place(item.offset);
            json!({
                "name": item.name,
                "kind": item.kind.label(),
                "line": line,
                "column": column,
            })
        })
        .collect()
}

fn declarations<'a>(input: &'a RuleInput<'_>) -> Vec<Definition<'a>> {
    let mut seen = HashSet::new();
    let mut found = Vec::new();
    for statement in &input.parsed.module().body {
        for item in statement_definitions(input, statement) {
            let private = item.name.starts_with('_')
                && !(item.name.starts_with("__") && item.name.ends_with("__"));
            if private && seen.insert((item.kind, item.name)) {
                found.push(item);
            }
        }
    }
    found
}

fn statement_definitions<'a>(input: &RuleInput<'_>, statement: &'a Stmt) -> Vec<Definition<'a>> {
    let single = |name, kind, offset| vec![Definition { name, kind, offset }];
    match statement {
        Stmt::ClassDef(node) => single(
            node.name.as_str(),
            Kind::Class,
            input.parsed.class_start(node),
        ),
        Stmt::FunctionDef(node) => single(
            node.name.as_str(),
            Kind::Function,
            input.parsed.function_start(node),
        ),
        Stmt::TypeAlias(node) => match node.name.as_ref() {
            Expr::Name(name) => single(name.id.as_str(), Kind::TypeAlias, node.start()),
            _ => Vec::new(),
        },
        Stmt::Assign(node) => constants(&node.targets),
        Stmt::AnnAssign(node) if node.value.is_some() => {
            constants(std::slice::from_ref(node.target.as_ref()))
        }
        _ => Vec::new(),
    }
}

fn constants(targets: &[Expr]) -> Vec<Definition<'_>> {
    targets
        .iter()
        .filter_map(|target| match target {
            Expr::Name(name) if is_upper(name.id.as_str()) => Some(Definition {
                name: name.id.as_str(),
                kind: Kind::Constant,
                offset: name.start(),
            }),
            _ => None,
        })
        .collect()
}
