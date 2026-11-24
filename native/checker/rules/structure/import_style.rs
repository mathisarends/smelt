//! IMP003: imports that break the package's relative or absolute style.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::edits::{fix, rewrite_from_module};
use crate::checker::analysis::imports::type_checking_lines;
use crate::checker::analysis::namespace::resolve_relative;
use crate::checker::analysis::nodes::walk_statements;
use crate::checker::contracts::PolicyOrigin;
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use ruff_python_ast::Stmt;
use ruff_text_size::Ranged;
use serde_json::{Value, json};

struct Style<'a> {
    origin: PolicyOrigin<'a>,
    relative: bool,
    max_level: usize,
}

/// One import statement of a source file.
struct Statement<'a> {
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    node: &'a Stmt,
    /// The dots and module of a from-import, as written.
    from_module: Option<String>,
    level: usize,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let include_guarded = context.model.config.import_graph.include_type_checking;
    let mut found = Vec::new();
    for (source, parsed) in context.sources {
        let Some(origin) = context.contracts.policy_for(&source.module, "imports") else {
            continue;
        };
        let style = Style {
            relative: origin.value["style"].as_str() != Some("absolute"),
            max_level: origin.value["max_relative_level"]
                .as_u64()
                .and_then(|level| usize::try_from(level).ok())
                .unwrap_or(1),
            origin,
        };
        let guarded = type_checking_lines(parsed);
        for node in walk_statements(parsed.module()) {
            let (targets, from_module, level) = match node {
                Stmt::ImportFrom(item) => {
                    let module = item.module.as_ref().map(|name| name.as_str());
                    let target =
                        resolve_relative(&source.module, source.is_package, item.level, module);
                    let level = usize::try_from(item.level).unwrap_or(usize::MAX);
                    let written = format!("{}{}", ".".repeat(level), module.unwrap_or_default());
                    (vec![target], Some(written), level)
                }
                Stmt::Import(item) => (
                    item.names
                        .iter()
                        .map(|alias| alias.name.to_string())
                        .collect(),
                    None,
                    0,
                ),
                _ => continue,
            };
            let (line, _) = parsed.text.position(usize::from(node.start()));
            if !include_guarded && guarded.contains(&line) {
                continue;
            }
            let statement = Statement {
                source,
                parsed,
                node,
                from_module,
                level,
            };
            for target in &targets {
                if context.model.is_first_party(target)
                    && let Some(finding) = check_target(&statement, target, &style)
                {
                    found.push(finding);
                }
            }
        }
    }
    found
}

fn check_target(
    statement: &Statement<'_>,
    target: &str,
    style: &Style<'_>,
) -> Option<StructureFinding> {
    let source = statement.source;
    let package = if source.is_package {
        source.module.as_str()
    } else {
        source
            .module
            .rsplit_once('.')
            .map_or("", |(parent, _)| parent)
    };
    let base = if statement.from_module.is_some() {
        target
    } else {
        target.rsplit_once('.').map_or("", |(parent, _)| parent)
    };
    let relative = relative_base(package, base)
        .filter(|(level, _)| style.relative && *level <= style.max_level)
        .map(|(_, spelling)| spelling);
    let current = statement.level;
    match relative {
        Some(_) if 0 < current && current <= style.max_level => return None,
        None if current == 0 => return None,
        _ => {}
    }
    let (expected_style, article, hint) = match &relative {
        Some(spelling) => (
            "relative",
            "a",
            format!("Use from {spelling} import ...; preserve names and aliases."),
        ),
        None => (
            "absolute",
            "an",
            format!("Use an absolute import from {target}."),
        ),
    };
    let spelling = relative.as_deref().unwrap_or(target);
    let text = &statement.parsed.text;
    let (line, column) = text.position(usize::from(statement.node.start()));
    let (end_line, end_column) = text.position(usize::from(statement.node.end()));
    let edit_fix = statement.from_module.as_deref().and_then(|written| {
        // Both spellings of a from-import bind the same names to the same objects.
        let edit =
            rewrite_from_module(text, usize::from(statement.node.start()), written, spelling)?;
        Some(fix("safe", &format!("Import from {spelling}"), vec![edit]))
    });
    Some(StructureFinding {
        message: format!(
            "{} expects {article} {expected_style} import of {target}",
            style.origin.label
        ),
        path: source.path.clone(),
        position: Some((line, column + 1)),
        end_line: Some(end_line),
        end_column: Some(end_column + 1),
        source_module: Some(source.module.clone()),
        target_module: Some(target.to_owned()),
        expected: json!({
            "contract": style.origin.contract,
            "style": expected_style,
            "max_relative_level": style.max_level,
            "relative_base": relative.as_deref().map_or(Value::Null, |item| json!(item)),
        }),
        hint,
        fix: edit_fix,
        ..StructureFinding::default()
    })
}

fn relative_base(package: &str, target: &str) -> Option<(usize, String)> {
    let source_parts: Vec<&str> = package.split('.').collect();
    let target_parts: Vec<&str> = if target.is_empty() {
        Vec::new()
    } else {
        target.split('.').collect()
    };
    let common = source_parts
        .iter()
        .zip(&target_parts)
        .take_while(|(source, target)| source == target)
        .count();
    if common == 0 {
        return None;
    }
    let level = source_parts.len() - common + 1;
    Some((
        level,
        format!("{}{}", ".".repeat(level), target_parts[common..].join(".")),
    ))
}
