//! VIS005: production code uses a private symbol or attribute of another module.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::imports::type_checking_lines;
use crate::checker::analysis::module_access::module_attributes;
use crate::checker::analysis::namespace::{
    Namespace, project_namespaces, resolve_dotted, resolve_export, resolve_relative,
};
use crate::checker::analysis::private_attributes::PrivateAttributeIndex;
use crate::checker::analysis::walk::{TreeNode, children, tree_nodes};
use crate::checker::files::SourceFile;
use crate::checker::patterns::is_within;
use crate::checker::source::ParsedSource;
use ruff_python_ast::{ModModule, Stmt};
use ruff_text_size::{Ranged, TextRange};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

const SYMBOL_HINT: &str = "Give the shared helper a module-public name without adding it to the \
package facade, or move it into the caller's module.";

const ATTRIBUTE_HINT: &str = "Use a deliberate public operation on the collaborator; keep \
justified cooperation with a narrow suppression or package policy.";

/// A private name reached through an import or a module-qualified read.
struct SymbolAccess<'a> {
    range: TextRange,
    base: String,
    name: &'a str,
    access: &'static str,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let namespaces = project_namespaces(context.sources, &context.files.packages);
    let ignore_guarded = !context.model.config.import_graph.include_type_checking;
    let mut index = PrivateAttributeIndex::new(&namespaces, context.sources);
    let mut sources: Vec<&(&SourceFile, &ParsedSource)> = context.sources.iter().collect();
    sources.sort_by(|left, right| left.0.module.cmp(&right.0.module));
    let mut found = Vec::new();
    for &&(source, parsed) in &sources {
        symbols(source, parsed, &namespaces, ignore_guarded, &mut found);
        attributes(context, source, parsed, &mut index, &mut found);
    }
    found
}

fn symbols(
    source: &SourceFile,
    parsed: &ParsedSource,
    namespaces: &HashMap<&str, Namespace>,
    ignore_guarded: bool,
    found: &mut Vec<StructureFinding>,
) {
    let guarded = type_checking_lines(parsed);
    let scoped = module_scope_imports(parsed.module());
    let mut accesses: Vec<SymbolAccess<'_>> = Vec::new();
    for node in tree_nodes(parsed.module()) {
        let TreeNode::Statement(statement @ Stmt::ImportFrom(node)) = node else {
            continue;
        };
        let line = parsed
            .text
            .line_at(usize::from(parsed.statement_start(statement)));
        if ignore_guarded && guarded.contains(&line) {
            continue;
        }
        let base = resolve_relative(
            &source.module,
            source.is_package,
            node.level,
            node.module.as_ref().map(|name| name.as_str()),
        );
        let module_scope = scoped.contains(&std::ptr::from_ref(statement).addr());
        for alias in &node.names {
            if source.is_package
                && module_scope
                && base != source.module
                && is_within(&base, &source.module)
                && alias
                    .asname
                    .as_ref()
                    .is_some_and(|name| !name.starts_with('_'))
            {
                continue;
            }
            if is_private(alias.name.as_str()) {
                accesses.push(SymbolAccess {
                    range: alias.range(),
                    base: base.clone(),
                    name: alias.name.as_str(),
                    access: "import",
                });
            }
        }
    }
    let ignored = if ignore_guarded {
        guarded
    } else {
        HashSet::new()
    };
    for read in module_attributes(parsed, &source.module, source.is_package, &ignored) {
        let attribute = read.node.attr.as_str();
        if !is_private(attribute) {
            continue;
        }
        if let Some((module, None)) = resolve_dotted(&read.qualified, namespaces) {
            accesses.push(SymbolAccess {
                range: read.node.range(),
                base: module,
                name: attribute,
                access: "read",
            });
        }
    }
    for access in accesses {
        let Some((module, Some(symbol))) = resolve_export(&access.base, access.name, namespaces)
        else {
            continue;
        };
        if module == source.module {
            continue;
        }
        let qualified = format!("{}.{}", access.base, access.name);
        let (line, column) = parsed.text.position(usize::from(access.range.start()));
        let (end_line, end_column) = parsed.text.position(usize::from(access.range.end()));
        found.push(StructureFinding {
            message: format!("production code accesses private module symbol {qualified}"),
            path: source.path.clone(),
            position: Some((line, column + 1)),
            end_line: Some(end_line),
            end_column: Some(end_column + 1),
            source_module: Some(source.module.clone()),
            target_module: Some(access.base.clone()),
            expected: json!({
                "kind": "symbol",
                "private_target": qualified,
                "defined_in": format!("{module}.{symbol}"),
                "access": access.access,
                "automatic_rewrite": false,
            }),
            hint: SYMBOL_HINT.to_owned(),
            ..StructureFinding::default()
        });
    }
}

fn attributes<'a>(
    context: &StructureContext<'_>,
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    index: &mut PrivateAttributeIndex<'a>,
    found: &mut Vec<StructureFinding>,
) {
    let boundary = context
        .contracts
        .policy_for(&source.module, "private_attribute_boundary")
        .and_then(|origin| origin.value.as_str())
        .unwrap_or("module")
        .to_owned();
    for access in index.accesses(source, parsed) {
        if access.owner_module == source.module {
            continue;
        }
        let Some(owner_source) = context.files.sources.get(&access.owner_module) else {
            continue;
        };
        if boundary == "package"
            && package(&source.module, source.is_package)
                == package(&access.owner_module, owner_source.is_package)
        {
            continue;
        }
        let target = format!("{}.{}", access.owner_class, access.attribute);
        found.push(StructureFinding {
            message: format!(
                "{} performs a private {} on {target}, owned by {}; expose an operation on its \
                 owner",
                source.module, access.operation, access.owner_module
            ),
            path: source.path.clone(),
            position: Some((access.line, access.column)),
            end_line: Some(access.end_line),
            end_column: Some(access.end_column),
            source_module: Some(source.module.clone()),
            target_module: Some(target),
            expected: json!({
                "kind": "attribute",
                "boundary": Value::from(boundary.as_str()),
                "owner_module": access.owner_module,
                "owner_class": access.owner_class,
                "receiver_type": access.receiver_type,
                "attribute": access.attribute,
                "operation": access.operation,
                "automatic_rewrite": false,
            }),
            hint: ATTRIBUTE_HINT.to_owned(),
            ..StructureFinding::default()
        });
    }
}

/// Import statements outside every function and class body.
fn module_scope_imports(module: &ModModule) -> HashSet<usize> {
    let mut found = HashSet::new();
    let mut pending: Vec<TreeNode<'_>> = module.body.iter().map(TreeNode::Statement).collect();
    while let Some(node) = pending.pop() {
        match node {
            TreeNode::Statement(statement @ (Stmt::Import(_) | Stmt::ImportFrom(_))) => {
                found.insert(std::ptr::from_ref(statement).addr());
            }
            TreeNode::Statement(Stmt::FunctionDef(_) | Stmt::ClassDef(_)) => {}
            other => pending.extend(children(other)),
        }
    }
    found
}

fn package(module: &str, is_package: bool) -> &str {
    if is_package {
        module
    } else {
        module.rsplit_once('.').map_or("", |(parent, _)| parent)
    }
}

fn is_private(name: &str) -> bool {
    name.starts_with('_') && !(name.starts_with("__") && name.ends_with("__"))
}
