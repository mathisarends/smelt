//! FWK001: `frameworks/prefer-secret-type`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{BindingCounts, Coverage, Names, qualified_name};
use crate::checker::analysis::nodes::{parse_eval, subscript_parts};
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, ModModule, Operator, Stmt, StmtClassDef};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

const SECRET_NAMES: [&str; 8] = [
    "password",
    "passwd",
    "secret",
    "api_key",
    "private_key",
    "access_token",
    "refresh_token",
    "token",
];
const SETTINGS: [&str; 3] = [
    "pydantic_settings.BaseSettings",
    "pydantic.BaseSettings",
    "pydantic.v1.BaseSettings",
];
const TYPING: [&str; 4] = [
    "typing.Optional",
    "typing.Union",
    "typing.Annotated",
    "typing_extensions.Annotated",
];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let imports = imports(module);
    let is_settings = |base: &Expr, names: &Names<'_>| {
        qualified_name(base, names).is_some_and(|name| SETTINGS.contains(&name.as_str()))
    };
    if !top_level(module).any(|class| class.bases().iter().any(|b| is_settings(b, &imports))) {
        return Vec::new();
    }
    let bound = BindingCounts::new(module, Coverage::Full);
    if bound.get("*") > 0 {
        return Vec::new();
    }
    let bindings: Names<'_> = imports
        .into_iter()
        .filter(|(name, _)| bound.get(name) == 1)
        .collect();
    let classes: BTreeMap<&str, &StmtClassDef> = top_level(module)
        .filter(|class| bound.get(class.name.as_str()) == 1 && class.decorator_list.is_empty())
        .map(|class| (class.name.as_str(), class))
        .collect();
    let mut settings: BTreeSet<&str> = BTreeSet::new();
    loop {
        let before = settings.len();
        for (name, class) in &classes {
            let inherits = class.bases().iter().any(|base| {
                is_settings(base, &bindings)
                    || matches!(base, Expr::Name(item) if settings.contains(item.id.as_str()))
            });
            if inherits {
                settings.insert(name);
            }
        }
        if settings.len() == before {
            break;
        }
    }
    let mut found = Vec::new();
    for owner in settings {
        for statement in &classes[owner].body {
            let Stmt::AnnAssign(node) = statement else {
                continue;
            };
            let Expr::Name(target) = node.target.as_ref() else {
                continue;
            };
            let lowered = target.id.as_str().to_lowercase();
            if !SECRET_NAMES.iter().any(|secret| {
                lowered == *secret
                    || lowered
                        .strip_suffix(secret)
                        .is_some_and(|prefix| prefix.ends_with('_'))
            }) {
                continue;
            }
            let plain = plain_types(&node.annotation, &bindings, &bound);
            if plain.is_empty() {
                continue;
            }
            let field = target.id.as_str();
            let suggested: Vec<&str> = plain
                .iter()
                .map(|kind| {
                    if *kind == "bytes" {
                        "SecretBytes"
                    } else {
                        "SecretStr"
                    }
                })
                .collect();
            let (line, column) = input.place(statement.start());
            found.push(input.violation(Finding {
                message: format!(
                    "review secret settings field {owner}.{field}; use a masked secret type"
                ),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "class": owner,
                    "field": field,
                    "plain_types": plain,
                    "suggested_types": suggested,
                    "automatic_rewrite": false,
                }),
                hint: "Use SecretStr/SecretBytes for credentials; review plaintext consumers and serialization explicitly.".to_owned(),
                fix: None,
            }));
        }
    }
    found
}

fn top_level(module: &ModModule) -> impl Iterator<Item = &StmtClassDef> {
    module.body.iter().filter_map(|statement| match statement {
        Stmt::ClassDef(node) => Some(node),
        _ => None,
    })
}

fn imports(module: &ModModule) -> Names<'_> {
    let mut result = Names::new();
    for statement in &module.body {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let name = alias.name.as_str();
                    if let Some(local) = &alias.asname {
                        result.insert(local.as_str(), name.to_owned());
                    } else {
                        let top = name.split('.').next().unwrap_or(name);
                        result.insert(top, top.to_owned());
                    }
                }
            }
            Stmt::ImportFrom(node) if node.level == 0 => {
                let source = node.module.as_ref().map_or("None", |name| name.as_str());
                for alias in &node.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    result.insert(local, format!("{source}.{}", alias.name));
                }
            }
            _ => {}
        }
    }
    result
}

fn plain_types(
    node: &Expr,
    bindings: &Names<'_>,
    bound: &BindingCounts<'_>,
) -> BTreeSet<&'static str> {
    match node {
        Expr::StringLiteral(item) => parse_eval(item.value.to_str())
            .map(|parsed| plain_types(parsed.expr(), bindings, bound))
            .unwrap_or_default(),
        Expr::Name(item) if bound.get(item.id.as_str()) == 0 => match item.id.as_str() {
            "str" => BTreeSet::from(["str"]),
            "bytes" => BTreeSet::from(["bytes"]),
            _ => BTreeSet::new(),
        },
        Expr::BinOp(item) if item.op == Operator::BitOr => {
            let mut types = plain_types(&item.left, bindings, bound);
            types.extend(plain_types(&item.right, bindings, bound));
            types
        }
        Expr::Subscript(item) => {
            let Some(wrapper) = qualified_name(&item.value, bindings) else {
                return BTreeSet::new();
            };
            if !TYPING.contains(&wrapper.as_str()) {
                return BTreeSet::new();
            }
            let mut parts = subscript_parts(&item.slice);
            if wrapper.ends_with("Annotated") {
                parts = &parts[..parts.len().min(1)];
            }
            parts
                .iter()
                .flat_map(|part| plain_types(part, bindings, bound))
                .collect()
        }
        _ => BTreeSet::new(),
    }
}
