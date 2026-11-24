//! IMP004: implementation imports of names a package already re-exports.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::edits::{fix, rewrite_from_module};
use crate::checker::analysis::imports::type_checking_lines;
use crate::checker::analysis::installed::{
    Exports, InstalledPackages, declares_all, definition_names,
};
use crate::checker::analysis::namespace::resolve_relative;
use crate::checker::analysis::nodes::walk_statements;
use crate::checker::analysis::walk::{TreeNode, tree_nodes};
use crate::checker::files::SourceFile;
use crate::checker::patterns::is_within;
use crate::checker::rules::exports::explicit_exports;
use crate::checker::source::{ParsedSource, SourceText};
use ruff_python_ast::{Alias, ExceptHandler, ModModule, Stmt, StmtImportFrom};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

pub(super) fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let facades = package_facades(context.sources);
    let first_party: HashSet<&str> = context.files.sources.keys().map(|name| top(name)).collect();
    let include_guarded = context.model.config.import_graph.include_type_checking;
    let mut installed = InstalledPackages::new(&context.files.root);
    let mut found = Vec::new();
    for (source, parsed) in context.sources.iter().chain(context.tests) {
        if source.is_package {
            continue;
        }
        let from_test = context.files.tests.contains_key(&source.path)
            || under_test_root(&source.path, context.test_roots);
        let guarded = type_checking_lines(parsed);
        for statement in walk_statements(parsed.module()) {
            let Stmt::ImportFrom(node) = statement else {
                continue;
            };
            if node
                .names
                .iter()
                .any(|alias| alias.name.starts_with('_') || alias.name.as_str() == "*")
            {
                continue;
            }
            let (line, _) = parsed.text.position(usize::from(node.start()));
            if !include_guarded && guarded.contains(&line) {
                continue;
            }
            let written = node.module.as_ref().map(|name| name.as_str());
            let base = resolve_relative(&source.module, source.is_package, node.level, written);
            let names: Vec<&str> = node.names.iter().map(|alias| alias.name.as_str()).collect();
            let package = if context.files.sources.contains_key(&base) {
                covering_package(&facades, &base, &source.module, &names, from_test)
            } else if node.level > 0 || first_party.contains(top(&base)) {
                continue;
            } else {
                let lookup = installed_facades(&mut installed, &base, context, &source.module);
                covering_package(&lookup, &base, &source.module, &names, from_test)
            };
            let Some(package) = package else {
                continue;
            };
            found.push(finding(source, parsed, node, &base, &package));
        }
    }
    found
}

fn finding(
    source: &SourceFile,
    parsed: &ParsedSource,
    node: &StmtImportFrom,
    base: &str,
    package: &str,
) -> StructureFinding {
    let mut import_from = package.to_owned();
    if node.level > 0 {
        let parts: Vec<&str> = node
            .module
            .as_ref()
            .map(|name| name.split('.').collect())
            .unwrap_or_default();
        let removed =
            base.split('.').count().cast_signed() - package.split('.').count().cast_signed();
        let remaining = parts.len().cast_signed() - removed;
        let level = usize::try_from(node.level).unwrap_or(usize::MAX);
        let extra = usize::try_from((-remaining).max(0)).unwrap_or(0);
        let kept = usize::try_from(remaining.max(0))
            .unwrap_or(0)
            .min(parts.len());
        import_from = format!("{}{}", ".".repeat(level + extra), parts[..kept].join("."));
    }
    let spellings: Vec<String> = node.names.iter().map(spelling).collect();
    let replacement = format!("from {import_from} import {}", spellings.join(", "));
    let edit_fix = package_fix(parsed, node, &import_from);
    let text = &parsed.text;
    let (line, column) = text.position(usize::from(node.start()));
    let (end_line, end_column) = text.position(usize::from(node.end()));
    let names: Vec<&str> = node.names.iter().map(|alias| alias.name.as_str()).collect();
    StructureFinding {
        message: format!("import these names from the existing {package} package API"),
        path: source.path.clone(),
        position: Some((line, column + 1)),
        end_line: Some(end_line),
        end_column: Some(end_column + 1),
        source_module: Some(source.module.clone()),
        target_module: Some(base.to_owned()),
        expected: json!({
            "package": package,
            "names": names,
            "replacement": replacement,
            "automatic_rewrite": edit_fix.is_some(),
        }),
        hint: format!(
            "Use {replacement}; combine imports from {package} in the same scope and guard, \
             preserving aliases and comments."
        ),
        fix: edit_fix,
        ..StructureFinding::default()
    }
}

/// The alias as written, like `ast.unparse` of the original alias.
fn spelling(alias: &Alias) -> String {
    match &alias.asname {
        Some(name) => format!("{} as {name}", alias.name),
        None => alias.name.to_string(),
    }
}

fn top(module: &str) -> &str {
    module.split('.').next().unwrap_or(module)
}

/// `Path(path).is_relative_to(root)` for a configured test root.
fn under_test_root(path: &str, roots: &[String]) -> bool {
    let components = |text: &str| -> Vec<String> {
        text.split(['/', '\\'])
            .filter(|part| !part.is_empty() && *part != ".")
            .map(|part| {
                if cfg!(windows) {
                    part.to_lowercase()
                } else {
                    part.to_owned()
                }
            })
            .collect()
    };
    let path = components(path);
    roots.iter().any(|root| path.starts_with(&components(root)))
}

/// Exports of each installed ancestor of `base` in a listed package.
fn installed_facades(
    installed: &mut InstalledPackages,
    base: &str,
    context: &StructureContext<'_>,
    module: &str,
) -> HashMap<String, Exports> {
    let listed = context
        .contracts
        .policy_for(module, "third_party_reexports")
        .and_then(|origin| origin.value.as_array())
        .is_some_and(|packages| packages.iter().any(|item| item.as_str() == Some(top(base))));
    if !listed {
        return HashMap::new();
    }
    let parts: Vec<&str> = base.split('.').collect();
    (1..parts.len())
        .map(|end| {
            let package = parts[..end].join(".");
            let exports = installed.exports(&package);
            (package, exports)
        })
        .collect()
}

/// The nearest ancestor of `base` whose facade re-exports all `names`.
///
/// Production callers inside a package never route through its own initializer.
pub fn covering_package(
    facades: &HashMap<String, Exports>,
    base: &str,
    caller: &str,
    names: &[&str],
    from_test: bool,
) -> Option<String> {
    let mut package = base.rsplit_once('.').map_or("", |(parent, _)| parent);
    while !package.is_empty() {
        let covers = facades.get(package).is_some_and(|exports| {
            names
                .iter()
                .all(|name| exports.get(*name) == Some(&format!("{base}.{name}")))
        });
        if (from_test || !is_within(caller, package)) && covers {
            return Some(package.to_owned());
        }
        package = package.rsplit_once('.').map_or("", |(parent, _)| parent);
    }
    None
}

/// Direct re-exports of every analyzed package initializer.
pub fn package_facades(sources: &[(&SourceFile, &ParsedSource)]) -> HashMap<String, Exports> {
    sources
        .iter()
        .filter(|(source, _)| source.is_package)
        .map(|(source, parsed)| {
            (
                source.module.clone(),
                direct_exports(source, parsed.module()),
            )
        })
        .collect()
}

/// Public name to imported name, excluding conditional or rebound bindings.
fn direct_exports(source: &SourceFile, tree: &ModModule) -> Exports {
    let mut result = Exports::new();
    let Some(exports) = explicit_exports(tree) else {
        return result;
    };
    let declares_all = declares_all(tree);
    let definitions = definition_names(tree);
    if definitions.contains("__getattr__") {
        return result;
    }
    let mut imports: HashMap<&str, usize> = HashMap::new();
    for node in tree_nodes(tree) {
        let (aliases, plain) = match node {
            TreeNode::Statement(Stmt::Import(item)) => (&item.names, true),
            TreeNode::Statement(Stmt::ImportFrom(item)) => (&item.names, false),
            _ => continue,
        };
        for alias in aliases {
            if alias.name.as_str() == "*" {
                return result;
            }
            let name = match &alias.asname {
                Some(name) => name.as_str(),
                None if plain => alias.name.split('.').next().unwrap_or_default(),
                None => alias.name.as_str(),
            };
            *imports.entry(name).or_insert(0) += 1;
        }
    }
    for statement in &tree.body {
        let Stmt::ImportFrom(node) = statement else {
            continue;
        };
        let base = resolve_relative(
            &source.module,
            true,
            node.level,
            node.module.as_ref().map(|name| name.as_str()),
        );
        for alias in &node.names {
            let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
            if name.starts_with('_')
                || definitions.contains(name)
                || imports.get(name) != Some(&1)
                || (declares_all && !exports.contains(name))
            {
                continue;
            }
            result.insert(name.to_owned(), format!("{base}.{}", alias.name));
        }
    }
    result
}

/// Import from the package, joining an import of it in the same block.
///
/// Unsafe: the package initializer now runs first, which can change import
/// order, side effects and cycles.
fn package_fix(parsed: &ParsedSource, node: &StmtImportFrom, import_from: &str) -> Option<Value> {
    let text = &parsed.text;
    let siblings = block_of(&parsed.module().body, node)?;
    let level = import_from.len() - import_from.trim_start_matches('.').len();
    let module = Some(import_from.trim_start_matches('.')).filter(|name| !name.is_empty());
    let target = siblings.iter().find_map(|sibling| match sibling {
        Stmt::ImportFrom(item)
            if !std::ptr::eq(item, node)
                && usize::try_from(item.level).ok() == Some(level)
                && item.module.as_ref().map(|name| name.as_str()) == module
                && item.names[0].name.as_str() != "*"
                && !text.segment(item.range()).contains('#') =>
        {
            Some(item)
        }
        _ => None,
    });
    let description = format!("Import from {import_from}");
    if let Some(target) = target
        && text.stands_alone(node.range())
    {
        return merge_into(text, node, target, &description);
    }
    let current = format!(
        "{}{}",
        ".".repeat(usize::try_from(node.level).unwrap_or(0)),
        node.module.as_ref().map_or("", |name| name.as_str())
    );
    let edit = rewrite_from_module(text, usize::from(node.start()), &current, import_from)?;
    Some(fix("unsafe", &description, vec![edit]))
}

fn merge_into(
    text: &SourceText,
    node: &StmtImportFrom,
    target: &StmtImportFrom,
    description: &str,
) -> Option<Value> {
    let lines = text.line_start(text.line_at(usize::from(node.start())))
        ..text.line_end(text.line_at(usize::from(node.end())));
    if text.text[lines].contains('#') {
        return None;
    }
    let present: HashSet<&str> = target
        .names
        .iter()
        .map(|alias| text.segment(alias.range()))
        .collect();
    let added: Vec<&str> = node
        .names
        .iter()
        .map(|alias| text.segment(alias.range()))
        .filter(|spelled| !present.contains(spelled))
        .collect();
    let mut edits = vec![text.delete_lines(node.range())];
    if !added.is_empty() {
        let end = usize::from(target.names.last()?.end());
        edits.push(text.insert(end, &format!(", {}", added.join(", "))));
    }
    Some(fix("unsafe", description, edits))
}

/// The statement list that directly holds `node`.
fn block_of<'a>(body: &'a [Stmt], node: &StmtImportFrom) -> Option<&'a [Stmt]> {
    if body
        .iter()
        .any(|statement| matches!(statement, Stmt::ImportFrom(item) if std::ptr::eq(item, node)))
    {
        return Some(body);
    }
    body.iter().find_map(|statement| {
        let mut blocks: Vec<&'a [Stmt]> = Vec::new();
        match statement {
            Stmt::FunctionDef(item) => blocks.push(&item.body),
            Stmt::ClassDef(item) => blocks.push(&item.body),
            Stmt::For(item) => blocks.extend([&item.body[..], &item.orelse[..]]),
            Stmt::While(item) => blocks.extend([&item.body[..], &item.orelse[..]]),
            Stmt::If(item) => {
                blocks.push(&item.body);
                blocks.extend(item.elif_else_clauses.iter().map(|clause| &clause.body[..]));
            }
            Stmt::With(item) => blocks.push(&item.body),
            Stmt::Match(item) => blocks.extend(item.cases.iter().map(|case| &case.body[..])),
            Stmt::Try(item) => {
                blocks.push(&item.body);
                blocks.extend(item.handlers.iter().map(|handler| {
                    let ExceptHandler::ExceptHandler(handler) = handler;
                    &handler.body[..]
                }));
                blocks.extend([&item.orelse[..], &item.finalbody[..]]);
            }
            _ => {}
        }
        blocks.into_iter().find_map(|block| block_of(block, node))
    })
}
