//! VIS011: package re-exports that bypass an immediate subpackage.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::bindings::{BindingCounts, Coverage};
use crate::checker::analysis::namespace::resolve_relative;
use crate::checker::files::{FileIndex, SourceFile};
use crate::checker::rules::exports::static_export_declarations;
use crate::checker::source::ParsedSource;
use ruff_python_ast::{Stmt, StmtImportFrom};
use ruff_text_size::Ranged;
use serde_json::json;

pub(super) fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    context
        .sources
        .iter()
        .filter(|(source, _)| source.is_package)
        .flat_map(|(source, parsed)| check_initializer(source, parsed, context.files))
        .collect()
}

fn check_initializer(
    source: &SourceFile,
    parsed: &ParsedSource,
    files: &FileIndex,
) -> Vec<StructureFinding> {
    let tree = parsed.module();
    let Some((exports, declarations)) = static_export_declarations(tree) else {
        return Vec::new();
    };
    let counts = BindingCounts::new(tree, Coverage::Full);
    if counts.get("*") != 0 || counts.get("__all__") != declarations {
        return Vec::new();
    }
    let mut found = Vec::new();
    for statement in &tree.body {
        let Stmt::ImportFrom(node) = statement else {
            continue;
        };
        let Some((base, child)) = bypassed_child(node, &source.module, files) else {
            continue;
        };
        let bindings: Vec<_> = node
            .names
            .iter()
            .filter_map(|alias| {
                let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                let public =
                    exports.contains(local) || (declarations == 0 && !local.starts_with('_'));
                (public && counts.get(local) == 1).then_some((alias.name.as_str(), local))
            })
            .collect();
        if bindings.is_empty() {
            continue;
        }
        let names: Vec<_> = bindings.iter().map(|(name, _)| *name).collect();
        let edges: Vec<_> = bindings
            .iter()
            .map(|(name, local)| format!("{}.{name} as {local}", base.module))
            .collect();
        let (line, column) = parsed.text.position(usize::from(node.start()));
        let (end_line, end_column) = parsed.text.position(usize::from(node.end()));
        let package = &child.module;
        found.push(StructureFinding {
            message: format!("this re-export bypasses the {package} package API"),
            path: source.path.clone(),
            position: Some((line, column + 1)),
            end_line: Some(end_line),
            end_column: Some(end_column + 1),
            source_module: Some(source.module.clone()),
            target_module: Some(base.module.clone()),
            expected: json!({"package": package, "names": names, "reexport_edges": edges}),
            hint: format!(
                "Expose {} in {} and re-export from {package} here. Preserve \
                 public names and check for import cycles.",
                names.join(", "),
                child.path,
            ),
            ..StructureFinding::default()
        });
    }
    found
}

fn bypassed_child<'a>(
    node: &StmtImportFrom,
    package: &str,
    files: &'a FileIndex,
) -> Option<(&'a SourceFile, &'a SourceFile)> {
    if usize::try_from(node.level).ok()? > package.split('.').count() {
        return None;
    }
    let written = node.module.as_ref()?.as_str();
    let base = resolve_relative(package, true, node.level, Some(written));
    let descendant = base.strip_prefix(package)?.strip_prefix('.')?;
    let (child_name, _) = descendant.split_once('.')?;
    let child = files
        .sources
        .get(&format!("{package}.{child_name}"))
        .filter(|file| file.is_package)?;
    Some((files.sources.get(&base)?, child))
}
