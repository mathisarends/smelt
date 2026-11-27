//! MOD013: exception classes and modules named with Exception instead of Error.
use super::{StructureContext, StructureFinding, last_segment};
use crate::checker::analysis::nodes::all_classes;
use ruff_python_ast::{Expr, StmtClassDef};
use serde_json::json;
use std::collections::HashSet;

const SUFFIX: &str = "Exception";
/// Builtin exception bases whose names do not end in Error, Exception or Warning.
const OTHER_BUILTINS: [&str; 7] = [
    "BaseExceptionGroup",
    "ExceptionGroup",
    "GeneratorExit",
    "KeyboardInterrupt",
    "StopAsyncIteration",
    "StopIteration",
    "SystemExit",
];
const MODULE_NAMES: [&str; 2] = ["exception", "exceptions"];
const MODULE_NAME: &str = "errors";

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let mut found = check_modules(context);
    for (source, parsed) in context.sources {
        let classes = all_classes(parsed.parsed.syntax());
        let exceptions = local_exceptions(&classes);
        for class in classes {
            let name = class.name.as_str();
            let Some(suggested) = error_name(name) else {
                continue;
            };
            if !exceptions.contains(name) {
                continue;
            }
            let (line, column) = parsed.text.position(usize::from(parsed.class_start(class)));
            found.push(StructureFinding {
                message: format!("exception class \"{name}\" ends in Exception instead of Error"),
                position: Some((line, column + 1)),
                expected: json!({
                    "kind": "class",
                    "name": name,
                    "suggested_name": suggested,
                    "automatic_rename": false,
                }),
                hint: format!(
                    "Rename the class to \"{suggested}\"; update references, except clauses and \
                     exports."
                ),
                ..super::naming::named(context, &source.module, source.path.clone())
            });
        }
    }
    found
}

fn check_modules(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let files = context.files;
    let packages = files.packages.iter().map(|name| {
        let path = files.path_for_module(name).unwrap_or(name).to_owned();
        (name, path, "package")
    });
    let modules = files
        .sources
        .iter()
        .filter(|(_, source)| !source.is_package)
        .map(|(name, source)| (name, source.path.clone(), "module"));
    let mut found = Vec::new();
    for (module, path, kind) in packages.chain(modules) {
        let segment = last_segment(module);
        let bare = segment.trim_start_matches('_');
        if !MODULE_NAMES.contains(&bare) {
            continue;
        }
        let shorter = format!("{}{MODULE_NAME}", &segment[..segment.len() - bare.len()]);
        let parent = module.rsplit_once('.').map_or("", |(parent, _)| parent);
        let target = if parent.is_empty() {
            shorter.clone()
        } else {
            format!("{parent}.{shorter}")
        };
        let occupied =
            files.sources.contains_key(&target) || files.packages.binary_search(&target).is_ok();
        let suggested = (!occupied).then(|| shorter.clone());
        let hint = match &suggested {
            Some(name) => {
                format!("Rename the {kind} to \"{name}\"; update imports and facade exports.")
            }
            None => format!(
                "Merge the {kind} into the existing \"{shorter}\" module or package; update \
                 imports and facade exports."
            ),
        };
        found.push(StructureFinding {
            message: format!("{kind} name \"{segment}\" names errors as exceptions ({module})"),
            expected: json!({
                "kind": kind,
                "name": segment,
                "suggested_name": suggested,
                "automatic_rename": false,
            }),
            hint,
            ..super::naming::named(context, module, path)
        });
    }
    found
}

/// `ParseError` for `ParseException`; `None` when no name precedes the suffix.
fn error_name(name: &str) -> Option<String> {
    let head = name.strip_suffix(SUFFIX)?;
    (!head.trim_matches('_').is_empty()).then(|| format!("{head}Error"))
}

/// The names of `classes` that derive from an exception, judged by their base names.
fn local_exceptions<'a>(classes: &[&'a StmtClassDef]) -> HashSet<&'a str> {
    let mut found: HashSet<&str> = HashSet::new();
    loop {
        let before = found.len();
        for class in classes {
            let derives = class.arguments.as_ref().is_some_and(|arguments| {
                arguments
                    .args
                    .iter()
                    .filter_map(base_name)
                    .any(|base| is_exception_name(base) || found.contains(base))
            });
            if derives {
                found.insert(class.name.as_str());
            }
        }
        if found.len() == before {
            return found;
        }
    }
}

fn base_name(base: &Expr) -> Option<&str> {
    match base {
        Expr::Name(node) => Some(node.id.as_str()),
        Expr::Attribute(node) => Some(node.attr.as_str()),
        Expr::Subscript(node) => base_name(&node.value),
        _ => None,
    }
}

fn is_exception_name(name: &str) -> bool {
    ["Error", "Exception", "Warning"]
        .iter()
        .any(|suffix| name.ends_with(suffix))
        || OTHER_BUILTINS.contains(&name)
}
