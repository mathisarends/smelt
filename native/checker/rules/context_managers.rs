//! TYP004: `typing/deprecated-async-contextmanager-return`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{BindingCounts, Coverage, Names, qualified_name};
use crate::checker::analysis::edits::{
    Skipped, add_from_import, fix, name_used, python_strip, remove_aliases, top_level_import,
};
use crate::checker::analysis::nodes::{parse_eval, walk_statements};
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ModModule, Stmt};
use ruff_text_size::Ranged;
use serde_json::{Value, json};

const DECORATOR: &str = "contextlib.asynccontextmanager";
const ITERATORS: [&str; 2] = ["collections.abc.AsyncIterator", "typing.AsyncIterator"];
const GENERATOR: &str = "AsyncGenerator";

/// Stable module-level imports, in the order the module binds them.
struct StableImports<'a> {
    ordered: Vec<(&'a str, String)>,
    names: Names<'a>,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let source = input.parsed.text.text.as_str();
    let imports = stable_imports(module);
    let mut found = Vec::new();
    for statement in walk_statements(module) {
        let Stmt::FunctionDef(function) = statement else {
            continue;
        };
        let decorated = function.decorator_list.iter().any(|decorator| {
            qualified_name(&decorator.expression, &imports.names).as_deref() == Some(DECORATOR)
        });
        if !function.is_async || !decorated {
            continue;
        }
        let Some(annotation) = function.returns.as_deref() else {
            continue;
        };
        let Some(replacement) = replacement(annotation, source, &imports.names) else {
            continue;
        };
        let repair = rewrite(input, annotation, &imports);
        let (line, column) = input.place(annotation.start());
        let (end_line, end_column) = input.place(annotation.end());
        found.push(input.violation(Finding {
            message: format!(
                "use {replacement} for @asynccontextmanager function {}",
                function.name
            ),
            line,
            column,
            end_line: Some(end_line),
            end_column: Some(end_column),
            expected: json!({
                "function": function.name.as_str(),
                "decorator": DECORATOR,
                "annotation": unparse(source, annotation),
                "suggested_annotation": replacement,
                "suggested_import": "from collections.abc import AsyncGenerator",
                "automatic_rewrite": repair.is_some(),
            }),
            hint: format!(
                "Annotate as {replacement} and import AsyncGenerator from collections.abc; keep @asynccontextmanager."
            ),
            fix: repair,
        }));
    }
    found
}

fn replacement(annotation: &Expr, source: &str, names: &Names<'_>) -> Option<String> {
    if let Expr::StringLiteral(node) = annotation {
        let value = node.value.to_str();
        let parsed = parse_eval(value)?;
        return described(parsed.expr(), value, names);
    }
    described(annotation, source, names)
}

fn described(node: &Expr, source: &str, names: &Names<'_>) -> Option<String> {
    let Expr::Subscript(item) = node else {
        return None;
    };
    if item.slice.is_tuple_expr() {
        return None;
    }
    let target = qualified_name(&item.value, names)?;
    ITERATORS
        .contains(&target.as_str())
        .then(|| format!("AsyncGenerator[{}, None]", unparse(source, &item.slice)))
}

/// Annotate as `AsyncGenerator[T, None]`, importing it like `AsyncIterator`.
///
/// Safe: the decorator's contract is unchanged and only the annotation differs.
fn rewrite(input: &RuleInput<'_>, annotation: &Expr, imports: &StableImports<'_>) -> Option<Value> {
    let text = &input.parsed.text;
    let mut edits = Vec::new();
    if let Expr::StringLiteral(literal) = annotation {
        let value = python_strip(literal.value.to_str());
        let parsed = parse_eval(value)?;
        let Expr::Subscript(node) = parsed.expr() else {
            return None;
        };
        let spelling = spelling(input, (&node.value, value), annotation, imports, &mut edits)?;
        let quote = text.segment(annotation.range()).chars().next()?;
        let rewritten = format!("{spelling}[{}, None]", unparse(value, &node.slice));
        edits.push(text.replace(annotation.range(), &format!("{quote}{rewritten}{quote}")));
    } else {
        let Expr::Subscript(node) = annotation else {
            return None;
        };
        let source = text.text.as_str();
        let spelling = spelling(
            input,
            (&node.value, source),
            annotation,
            imports,
            &mut edits,
        )?;
        edits.push(text.replace(node.value.range(), &spelling));
        edits.push(text.insert(usize::from(node.slice.end()), ", None"));
    }
    Some(fix(
        "safe",
        "Annotate the context manager as AsyncGenerator[T, None]",
        edits,
    ))
}

fn spelling(
    input: &RuleInput<'_>,
    (value, source): (&Expr, &str),
    annotation: &Expr,
    imports: &StableImports<'_>,
    edits: &mut Vec<Value>,
) -> Option<String> {
    match value {
        // typing.AsyncIterator and abc.AsyncIterator name their module already.
        Expr::Attribute(node) => Some(format!("{}.{GENERATOR}", unparse(source, &node.value))),
        Expr::Name(node) => generator_name(input, node.id.as_str(), annotation, imports, edits),
        _ => None,
    }
}

/// The local name for `AsyncGenerator`; adds the import edits it needs.
///
/// An existing import is reused. Otherwise the `AsyncIterator` alias is renamed
/// when nothing else names it, or `AsyncGenerator` joins its import.
fn generator_name(
    input: &RuleInput<'_>,
    local: &str,
    annotation: &Expr,
    imports: &StableImports<'_>,
    edits: &mut Vec<Value>,
) -> Option<String> {
    let parsed = input.parsed;
    let text = &parsed.text;
    let qualified = imports.names.get(local)?;
    let module = qualified.rsplit_once('.').map_or("", |parts| parts.0);
    let wanted = format!("{module}.{GENERATOR}");
    let existing = imports
        .ordered
        .iter()
        .find(|(_, target)| *target == wanted)
        .map(|(name, _)| *name);
    let found = top_level_import(parsed.module(), local);
    let unused = found.is_some() && !name_used(parsed, local, &[Skipped::Expression(annotation)]);
    if let Some(existing) = existing {
        if unused
            && let Some((statement, alias)) = found
            && let Some(removal) = remove_aliases(text, statement, &[alias])
        {
            edits.push(removal);
        }
        return Some(existing.to_owned());
    }
    if name_used(parsed, GENERATOR, &[]) {
        return None;
    }
    let (statement, alias) = found?;
    if unused && alias.asname.is_none() && matches!(statement, Stmt::ImportFrom(_)) {
        let start = usize::from(alias.start());
        let length: usize = text.text[start..]
            .chars()
            .take(alias.name.chars().count())
            .map(char::len_utf8)
            .sum();
        edits.push(text.edit(start, start + length, GENERATOR));
    } else {
        edits.push(add_from_import(parsed, module, GENERATOR));
    }
    Some(GENERATOR.to_owned())
}

fn stable_imports(module: &ModModule) -> StableImports<'_> {
    let bound = BindingCounts::new(module, Coverage::Full);
    let mut walk = ImportWalk {
        bound: &bound,
        found: Vec::new(),
    };
    if bound.get("*") == 0 {
        walk.visit_body(&module.body);
    }
    let ordered = walk.found;
    let names = ordered
        .iter()
        .map(|(name, target)| (*name, target.clone()))
        .collect();
    StableImports { ordered, names }
}

/// Imports at module level, including guarded ones, outside any definition.
struct ImportWalk<'a, 'b> {
    bound: &'b BindingCounts<'a>,
    found: Vec<(&'a str, String)>,
}

impl<'a> Visitor<'a> for ImportWalk<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => {}
            Stmt::Import(node) => {
                for alias in &node.names {
                    let local = match &alias.asname {
                        Some(name) => name.as_str(),
                        None => alias.name.split('.').next().unwrap_or_default(),
                    };
                    if self.bound.get(local) == 1 {
                        let target = if alias.asname.is_some() {
                            alias.name.as_str()
                        } else {
                            local
                        };
                        self.found.push((local, target.to_owned()));
                    }
                }
            }
            Stmt::ImportFrom(node) if node.level == 0 => {
                let source = node.module.as_ref().map_or("", |name| name.as_str());
                for alias in &node.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    if self.bound.get(local) == 1 {
                        self.found.push((local, format!("{source}.{}", alias.name)));
                    }
                }
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, _: &'a Expr) {}
}
