//! FWK002: `frameworks/redundant-dishka-provider`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{BindingCounts, Coverage, Names};
use crate::checker::analysis::unparse::{unparse, unparse_keyword};
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, ExprCall, Keyword, ModModule, Stmt, StmtFunctionDef};
use serde_json::json;
use std::collections::HashSet;

const OPTIONS: [&str; 4] = ["scope", "cache", "provides", "override"];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let bindings = dishka_bindings(module);
    let mut found = Vec::new();
    for statement in &module.body {
        let Stmt::ClassDef(owner) = statement else {
            continue;
        };
        let [base] = owner.bases() else {
            continue;
        };
        if !owner.decorator_list.is_empty()
            || qualified(base, &bindings).as_deref() != Some("dishka.Provider")
        {
            continue;
        }
        for member in &owner.body {
            let Stmt::FunctionDef(node) = member else {
                continue;
            };
            if let Some(violation) = candidate(input, owner.name.as_str(), node, &bindings) {
                found.push(violation);
            }
        }
    }
    found
}

fn candidate(
    input: &RuleInput<'_>,
    owner: &str,
    node: &StmtFunctionDef,
    bindings: &Names<'_>,
) -> Option<Violation> {
    let ([decorator], [Stmt::Return(returned)], false) = (
        node.decorator_list.as_slice(),
        node.body.as_slice(),
        node.is_async,
    ) else {
        return None;
    };
    let Expr::Call(call) = returned.value.as_deref()? else {
        return None;
    };
    let (marker, options): (&Expr, &[Keyword]) = match &decorator.expression {
        Expr::Call(item) => {
            if !item.arguments.args.is_empty() {
                return None;
            }
            (&item.func, &item.arguments.keywords)
        }
        other => (other, &[]),
    };
    if qualified(marker, bindings).as_deref() != Some("dishka.provide")
        || options.iter().any(|option| {
            option
                .arg
                .as_ref()
                .is_none_or(|name| !OPTIONS.contains(&name.as_str()))
        })
    {
        return None;
    }
    let dependencies = dependencies(node)?;
    let returns = node.returns.as_deref()?;
    if !matches!(call.func.as_ref(), Expr::Name(_) | Expr::Attribute(_)) {
        return None;
    }
    let source = input.parsed.text.text.as_str();
    let constructor = unparse(source, &call.func);
    let last = constructor.rsplit('.').next().unwrap_or(&constructor);
    if !last.chars().next().is_some_and(char::is_uppercase) || !forwards_all(call, &dependencies) {
        return None;
    }
    let mut arguments = vec![constructor.clone()];
    arguments.extend(options.iter().map(|option| unparse_keyword(source, option)));
    if !options
        .iter()
        .any(|option| option.arg.as_ref().is_some_and(|name| name == "provides"))
    {
        arguments.push(format!("provides={}", unparse(source, returns)));
    }
    let registration = format!("provide({})", arguments.join(", "));
    let name = node.name.as_str();
    let (line, column) = input.place(input.parsed.function_start(node));
    Some(input.violation(Finding {
        message: format!("review direct constructor registration for {owner}.{name}"),
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({
            "class": owner,
            "method": name,
            "constructor": constructor,
            "dependencies": dependencies,
            "suggested_registration": registration,
            "automatic_rewrite": false,
        }),
        hint: format!(
            "Review {name} = {registration}; verify constructor hints and preserve scope/cache/dependency markers."
        ),
        fix: None,
    }))
}

fn dependencies(node: &StmtFunctionDef) -> Option<Vec<&str>> {
    let signature = &node.parameters;
    if !signature.posonlyargs.is_empty()
        || signature.vararg.is_some()
        || signature.kwarg.is_some()
        || signature
            .args
            .iter()
            .chain(&signature.kwonlyargs)
            .any(|item| item.default.is_some())
        || signature
            .args
            .first()
            .is_none_or(|first| first.parameter.name.as_str() != "self")
    {
        return None;
    }
    let parameters: Vec<_> = signature.args[1..]
        .iter()
        .chain(&signature.kwonlyargs)
        .collect();
    if parameters
        .iter()
        .any(|item| item.parameter.annotation.is_none())
        || !matches!(
            node.returns.as_deref(),
            Some(Expr::Name(_) | Expr::Attribute(_))
        )
    {
        return None;
    }
    Some(
        parameters
            .iter()
            .map(|item| item.parameter.name.as_str())
            .collect(),
    )
}

fn forwards_all(call: &ExprCall, dependencies: &[&str]) -> bool {
    let mut positional = Vec::with_capacity(call.arguments.args.len());
    for argument in &call.arguments.args {
        let Expr::Name(name) = argument else {
            return false;
        };
        positional.push(name.id.as_str());
    }
    if positional.len() > dependencies.len() || positional != dependencies[..positional.len()] {
        return false;
    }
    let mut forwarded = positional;
    for keyword in &call.arguments.keywords {
        let (Some(argument), Expr::Name(value)) = (&keyword.arg, &keyword.value) else {
            return false;
        };
        if argument.as_str() != value.id.as_str() {
            return false;
        }
        forwarded.push(argument.as_str());
    }
    forwarded.len() == dependencies.len()
        && forwarded.iter().collect::<HashSet<_>>() == dependencies.iter().collect::<HashSet<_>>()
}

fn qualified(node: &Expr, bindings: &Names<'_>) -> Option<String> {
    match node {
        Expr::Name(item) => bindings.get(item.id.as_str()).cloned(),
        Expr::Attribute(item) => {
            let Expr::Name(owner) = item.value.as_ref() else {
                return None;
            };
            bindings
                .get(owner.id.as_str())
                .map(|root| format!("{root}.{}", item.attr))
        }
        _ => None,
    }
}

fn dishka_bindings(module: &ModModule) -> Names<'_> {
    let mut result = Names::new();
    for statement in &module.body {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    if alias.name.as_str() == "dishka" {
                        let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                        result.insert(local, "dishka".to_owned());
                    }
                }
            }
            Stmt::ImportFrom(node)
                if node.level == 0 && node.module.as_ref().is_some_and(|name| name == "dishka") =>
            {
                for alias in &node.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    result.insert(local, format!("dishka.{}", alias.name));
                }
            }
            _ => {}
        }
    }
    if result.is_empty() {
        return result;
    }
    let bound = BindingCounts::new(module, Coverage::Full);
    if bound.get("*") > 0 {
        return Names::new();
    }
    result.retain(|name, _| bound.get(name) == 1);
    result
}
