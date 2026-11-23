//! IDM004: `idioms/redundant-name-alias`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::dotted_name;
use crate::checker::analysis::nodes::is_upper;
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, ModModule, Stmt};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{HashMap, HashSet};

const TYPING: [&str; 2] = ["typing", "typing_extensions"];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let targets = Targets::new(module);
    let mut found = Vec::new();
    for statement in &module.body {
        let Some((name, value, form)) = alias(statement) else {
            continue;
        };
        let Some((kind, spelled)) = targets.describe(value) else {
            continue;
        };
        if name == spelled {
            continue;
        }
        let (line, column) = input.place(statement.start());
        found.push(input.violation(Finding {
            message: format!("{name} is a second name for {kind} {spelled}"),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "alias": name,
                "target": spelled,
                "target_kind": kind,
                "form": form,
                "automatic_rewrite": false,
            }),
            hint: format!("Use {spelled} directly and remove {name}."),
            fix: None,
        }));
    }
    found
}

fn alias(statement: &Stmt) -> Option<(&str, &Expr, &'static str)> {
    match statement {
        Stmt::Assign(node) => match node.targets.as_slice() {
            [Expr::Name(target)] => Some((target.id.as_str(), &node.value, "assignment")),
            _ => None,
        },
        Stmt::AnnAssign(node) => {
            let Expr::Name(target) = node.target.as_ref() else {
                return None;
            };
            let value = node.value.as_deref()?;
            let annotation = dotted_name(&node.annotation)?;
            matches!(annotation.as_str(), "TypeAlias" | "typing.TypeAlias").then_some((
                target.id.as_str(),
                value,
                "TypeAlias",
            ))
        }
        Stmt::TypeAlias(node) if node.type_params.is_none() => match node.name.as_ref() {
            Expr::Name(target) => Some((target.id.as_str(), &node.value, "type")),
            _ => None,
        },
        _ => None,
    }
}

fn cap_words(name: &str) -> bool {
    let mut buffer = [0; 4];
    name.chars()
        .next()
        .is_some_and(|first| is_upper(first.encode_utf8(&mut buffer)))
        && !is_upper(name)
}

/// Classes and functions one module defines, and the names it imports.
struct Targets<'a> {
    definitions: HashMap<&'a str, &'static str>,
    imported: HashSet<&'a str>,
    modules: HashSet<&'a str>,
}

impl<'a> Targets<'a> {
    fn new(tree: &'a ModModule) -> Self {
        let mut definitions = HashMap::new();
        let mut imported = HashSet::new();
        let mut modules = HashSet::new();
        let mut rebound = Vec::new();
        for statement in &tree.body {
            let definition = match statement {
                Stmt::ClassDef(node) => Some((node.name.as_str(), "class")),
                Stmt::FunctionDef(node) => Some((node.name.as_str(), "function")),
                Stmt::ImportFrom(node)
                    if !node
                        .module
                        .as_ref()
                        .is_some_and(|name| TYPING.contains(&name.as_str())) =>
                {
                    imported.extend(
                        node.names
                            .iter()
                            .filter(|alias| alias.name.as_str() != "*")
                            .map(|alias| alias.asname.as_ref().unwrap_or(&alias.name).as_str()),
                    );
                    None
                }
                Stmt::Import(node) => {
                    modules.extend(
                        node.names
                            .iter()
                            .filter(|alias| !TYPING.contains(&alias.name.as_str()))
                            .map(|alias| match &alias.asname {
                                Some(local) => local.as_str(),
                                None => {
                                    let full = alias.name.as_str();
                                    full.split('.').next().unwrap_or(full)
                                }
                            }),
                    );
                    None
                }
                _ => None,
            };
            if let Some((name, kind)) = definition
                && definitions.insert(name, kind).is_some()
            {
                rebound.push(name);
            }
        }
        for name in rebound {
            definitions.remove(name);
        }
        Self {
            definitions,
            imported,
            modules,
        }
    }

    fn describe(&self, value: &Expr) -> Option<(&'static str, String)> {
        let spelled = dotted_name(value)?;
        if matches!(value, Expr::Name(_)) {
            if let Some(kind) = self.definitions.get(spelled.as_str()) {
                return Some((kind, spelled));
            }
            let imported = self.imported.contains(spelled.as_str()) && cap_words(&spelled);
            return imported.then_some(("class", spelled));
        }
        let head = spelled.split('.').next().unwrap_or(&spelled);
        let last = spelled.rsplit('.').next().unwrap_or(&spelled);
        let class = self.modules.contains(head) && cap_words(last);
        class.then_some(("class", spelled))
    }
}
