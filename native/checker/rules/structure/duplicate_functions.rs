//! CPX004: module-level functions whose code repeats another function's.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::dump::{dump_function_shape, sha256_hex};
use crate::checker::analysis::edits::is_docstring;
use crate::checker::analysis::walk::{TreeNode, nodes_below};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::{Stmt, StmtFunctionDef};
use serde_json::json;
use std::collections::BTreeMap;

// Smaller bodies are too generic to be worth a shared module.
const MIN_STATEMENTS: usize = 3;
const FINGERPRINT_LENGTH: usize = 16;
const MAX_LOCATIONS: usize = 5;

struct FunctionCopy<'a> {
    source: &'a SourceFile,
    name: &'a str,
    line: usize,
    column: usize,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let mut sources: Vec<&(&SourceFile, &ParsedSource)> = context.sources.iter().collect();
    sources.sort_by(|left, right| left.0.path.cmp(&right.0.path));
    let scanned: Vec<Vec<(String, FunctionCopy<'_>)>> = sources
        .par_iter()
        .map(|(source, parsed)| fingerprints(source, parsed))
        .collect();
    let mut groups: BTreeMap<&str, BTreeMap<String, Vec<FunctionCopy<'_>>>> = BTreeMap::new();
    for (entry, functions) in sources.iter().zip(scanned) {
        let module = entry.0.module.as_str();
        let package = module.split('.').next().unwrap_or(module);
        let package = groups.entry(package).or_default();
        for (fingerprint, copy) in functions {
            package.entry(fingerprint).or_default().push(copy);
        }
    }
    let mut found = Vec::new();
    for package in groups.values() {
        for (fingerprint, copies) in package {
            if copies.len() >= 2 {
                report(fingerprint, copies, &mut found);
            }
        }
    }
    found
}

fn fingerprints<'a>(
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
) -> Vec<(String, FunctionCopy<'a>)> {
    let mut found = Vec::new();
    for statement in &parsed.module().body {
        let Stmt::FunctionDef(function) = statement else {
            continue;
        };
        let Some(fingerprint) = fingerprint(&parsed.text.text, function) else {
            continue;
        };
        let (line, column) = parsed
            .text
            .position(usize::from(parsed.function_start(function)));
        found.push((
            fingerprint,
            FunctionCopy {
                source,
                name: function.name.as_str(),
                line,
                column: column + 1,
            },
        ));
    }
    found
}

fn fingerprint(source: &str, function: &StmtFunctionDef) -> Option<String> {
    let body = match function.body.split_first() {
        Some((first, rest)) if is_docstring(first) => rest,
        _ => function.body.as_slice(),
    };
    let statements: usize = body
        .iter()
        .map(|statement| {
            nodes_below(TreeNode::Statement(statement))
                .iter()
                .filter(|node| matches!(node, TreeNode::Statement(_) | TreeNode::Elif(_)))
                .count()
        })
        .sum();
    if statements < MIN_STATEMENTS {
        return None;
    }
    // Names, docstrings and positions do not change what the function does.
    let mut digest = sha256_hex(&dump_function_shape(source, function, body));
    digest.truncate(FINGERPRINT_LENGTH);
    Some(digest)
}

fn report(fingerprint: &str, copies: &[FunctionCopy<'_>], found: &mut Vec<StructureFinding>) {
    let modules: Vec<&str> = copies
        .iter()
        .map(|copy| copy.source.module.as_str())
        .collect();
    let owner = common_package(&modules);
    for copy in copies {
        let others: Vec<&FunctionCopy<'_>> = copies
            .iter()
            .filter(|other| !std::ptr::eq(*other, copy))
            .collect();
        let first = others[0];
        let more = if others.len() > 1 {
            format!(" and {} more", others.len() - 1)
        } else {
            String::new()
        };
        let listed: Vec<_> = others
            .iter()
            .take(MAX_LOCATIONS)
            .map(|other| {
                json!({
                    "path": other.source.path,
                    "line": other.line,
                    "function": other.name,
                })
            })
            .collect();
        found.push(StructureFinding {
            message: format!(
                "Function {} repeats {} in {}{more}",
                copy.name, first.name, first.source.path
            ),
            path: copy.source.path.clone(),
            position: Some((copy.line, copy.column)),
            source_module: Some(copy.source.module.clone()),
            expected: json!({
                "function": copy.name,
                "fingerprint": fingerprint,
                "count": copies.len(),
                "copies": listed,
                "copies_truncated": others.len() > MAX_LOCATIONS,
                "common_package": owner,
                "automatic_rewrite": false,
            }),
            hint: format!(
                "Keep one copy in a shared module under {owner} and import it from every caller."
            ),
            ..StructureFinding::default()
        });
    }
}

fn common_package(modules: &[&str]) -> String {
    let parts: Vec<Vec<&str>> = modules
        .iter()
        .map(|module| {
            let mut segments: Vec<&str> = module.split('.').collect();
            if segments.len() > 1 {
                segments.pop();
            }
            segments
        })
        .collect();
    let shortest = parts.iter().map(Vec::len).min().unwrap_or(0);
    let mut common = Vec::new();
    for index in 0..shortest {
        let segment = parts[0][index];
        if parts.iter().any(|item| item[index] != segment) {
            break;
        }
        common.push(segment);
    }
    if common.is_empty() {
        parts[0][0].to_owned()
    } else {
        common.join(".")
    }
}
