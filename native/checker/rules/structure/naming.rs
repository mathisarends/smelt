//! LAY001, LAY003, LAY009 and MOD002: names that say too little or repeat themselves.
use super::{StructureContext, StructureFinding, last_segment};
use crate::checker::analysis::nodes::all_classes;
use crate::checker::files::is_identifier;
use serde_json::json;
use std::collections::BTreeSet;

const MIN_PLURAL_LENGTH: usize = 4;
const NON_PLURAL_ENDINGS: [&str; 9] =
    ["ss", "us", "is", "ies", "ses", "xes", "zes", "ches", "shes"];
const KEYWORDS: [&str; 35] = [
    "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import",
    "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
    "with", "yield",
];

/// A finding about a named module or package, placed in its architecture role.
fn named(context: &StructureContext<'_>, module: &str, path: String) -> StructureFinding {
    let info = context.model.info(module);
    StructureFinding {
        path,
        source_module: Some(module.to_owned()),
        feature: info.as_ref().and_then(|info| info.feature.clone()),
        layer: info.as_ref().and_then(|info| info.layer.clone()),
        ..StructureFinding::default()
    }
}

fn package_path(context: &StructureContext<'_>, package: &str) -> String {
    context
        .files
        .path_for_module(package)
        .unwrap_or(package)
        .to_owned()
}

pub fn check_forbidden_names(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let forbidden: BTreeSet<&str> = context.forbidden_names.iter().map(String::as_str).collect();
    if forbidden.is_empty() {
        return Vec::new();
    }
    let mut found = Vec::new();
    for package in &context.files.packages {
        let segment = last_segment(package);
        if forbidden.contains(segment) {
            found.push(StructureFinding {
                message: format!("package name \"{segment}\" is not allowed ({package})"),
                expected: json!({"forbidden_names": forbidden}),
                hint: "Name the package after what it does.".to_owned(),
                ..named(context, package, package_path(context, package))
            });
        }
    }
    for (name, source) in &context.files.sources {
        let segment = last_segment(name);
        if source.is_package || !forbidden.contains(segment) {
            continue;
        }
        found.push(StructureFinding {
            message: format!("module name \"{segment}\" is not allowed ({name})"),
            expected: json!({"forbidden_names": forbidden}),
            hint: "Name the module after what it does, or move its functions next to their \
                   callers."
                .to_owned(),
            ..named(context, name, source.path.clone())
        });
    }
    found
}

pub fn check_private_modules(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let packages = context
        .files
        .packages
        .iter()
        .map(|name| (name, package_path(context, name), "package"));
    let modules = context
        .files
        .sources
        .iter()
        .filter(|(name, source)| !source.is_package && last_segment(name) != "__main__")
        .map(|(name, source)| (name, source.path.clone(), "module"));
    let mut found = Vec::new();
    for (name, path, kind) in packages.chain(modules) {
        let segment = last_segment(name);
        if !segment.starts_with('_') {
            continue;
        }
        let Some(origin) = context.contracts.policy_for(name, "private_modules") else {
            continue;
        };
        if origin.value.as_str() != Some("forbid") {
            continue;
        }
        found.push(StructureFinding {
            message: format!(
                "{} forbids underscore-prefixed {kind} name \"{segment}\" ({name})",
                origin.label
            ),
            expected: json!({
                "contract": origin.contract,
                "private_modules": "forbid",
                "kind": kind,
                "name": segment,
            }),
            hint: format!(
                "Rename the {kind} after its responsibility without a leading underscore; update \
                 imports and facade exports. Private helpers inside a normal module may keep \
                 their underscore prefix."
            ),
            ..named(context, name, path)
        });
    }
    found
}

pub fn check_redundant_names(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let files = context.files;
    let mut found = Vec::new();
    for (module, source) in &files.sources {
        if source.is_package {
            continue;
        }
        let Some(repeat) = redundant_context(module) else {
            continue;
        };
        let parent = module.rsplit_once('.').map_or("", |(parent, _)| parent);
        let target = format!("{parent}.{}", repeat.shorter);
        let occupied =
            files.sources.contains_key(&target) || files.packages.binary_search(&target).is_ok();
        let suggested = (!occupied).then(|| format!("{}.py", repeat.shorter));
        let hint = match &suggested {
            Some(name) => {
                format!(
                    "Review renaming this file to \"{name}\"; update imports and facade exports."
                )
            }
            None => "Choose a responsibility-based filename; the shorter name is already occupied \
                     by a module or package. Update imports and facade exports."
                .to_owned(),
        };
        found.push(StructureFinding {
            message: format!(
                "module name \"{}\" repeats \"{}\" from package \"{}\"",
                last_segment(module),
                repeat.repeated,
                repeat.ancestor
            ),
            expected: json!({
                "ancestor": repeat.ancestor,
                "repeated_name": repeat.repeated,
                "suggested_name": suggested,
                "automatic_rename": false,
            }),
            hint,
            ..named(context, module, source.path.clone())
        });
    }
    found
}

struct Repeat {
    ancestor: String,
    repeated: String,
    shorter: String,
}

fn redundant_context(module: &str) -> Option<Repeat> {
    let parts: Vec<&str> = module.split('.').collect();
    let name = *parts.last()?;
    if name.starts_with("__") && name.ends_with("__") {
        return None;
    }
    let bare = name.trim_start_matches('_');
    let private_prefix = &name[..name.len() - bare.len()];
    for end in (1..parts.len()).rev() {
        let context = parts[end - 1].trim_start_matches('_');
        if context.is_empty() {
            continue;
        }
        let mut variants = vec![context];
        if context.chars().count() >= MIN_PLURAL_LENGTH
            && context.ends_with('s')
            && !NON_PLURAL_ENDINGS
                .iter()
                .any(|ending| context.ends_with(ending))
        {
            variants.push(&context[..context.len() - 1]);
        }
        for repeated in variants {
            let shorter = bare
                .strip_prefix(repeated)
                .and_then(|rest| rest.strip_prefix('_'))
                .or_else(|| {
                    bare.strip_suffix(repeated)
                        .and_then(|rest| rest.strip_suffix('_'))
                });
            if let Some(shorter) = shorter
                && !shorter.is_empty()
                && is_identifier(shorter)
                && !KEYWORDS.contains(&shorter)
            {
                return Some(Repeat {
                    ancestor: parts[..end].join("."),
                    repeated: repeated.to_owned(),
                    shorter: format!("{private_prefix}{shorter}"),
                });
            }
        }
    }
    None
}

pub fn check_base_affixes(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let mut found = Vec::new();
    for (source, parsed) in context.sources {
        for class in all_classes(parsed.parsed.syntax()) {
            let name = class.name.as_str();
            let Some(affix) = base_affix(name) else {
                continue;
            };
            let (line, column) = parsed.text.position(usize::from(parsed.class_start(class)));
            found.push(StructureFinding {
                message: format!("class \"{name}\" uses the forbidden Base {affix}"),
                position: Some((line, column + 1)),
                expected: json!({
                    "forbidden_affix": "Base",
                    "position": affix,
                    "class": name,
                    "automatic_rename": false,
                }),
                hint: "Name the class after its responsibility or contract instead of its \
                       inheritance role; update references and exports."
                    .to_owned(),
                ..named(context, &source.module, source.path.clone())
            });
        }
    }
    found
}

fn base_affix(name: &str) -> Option<&'static str> {
    let name = name.trim_start_matches('_');
    if let Some(rest) = name.strip_prefix("Base")
        && rest
            .chars()
            .next()
            .is_none_or(|next| next.is_uppercase() || next == '_')
    {
        return Some("prefix");
    }
    let head = name.strip_suffix("Base")?;
    (!head.trim_matches('_').is_empty()).then_some("suffix")
}
