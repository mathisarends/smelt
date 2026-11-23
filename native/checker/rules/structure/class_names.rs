//! MOD009: public classes sharing a name across the modules of one package.
use super::{StructureContext, StructureFinding};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use ruff_python_ast::Stmt;
use serde_json::json;
use std::collections::{BTreeMap, HashMap};

struct Definition<'a> {
    source: &'a SourceFile,
    line: usize,
    column: usize,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let parsed: HashMap<&str, &ParsedSource> = context
        .sources
        .iter()
        .map(|(source, parsed)| (source.module.as_str(), *parsed))
        .collect();
    let mut groups: BTreeMap<&str, BTreeMap<&str, Vec<Definition<'_>>>> = BTreeMap::new();
    for module in &context.files.source_order {
        let source = &context.files.sources[module];
        let tree = parsed[module.as_str()];
        let package = if source.is_package {
            source.module.as_str()
        } else {
            source
                .module
                .rsplit_once('.')
                .map_or("", |(parent, _)| parent)
        };
        for statement in &tree.parsed.syntax().body {
            let Stmt::ClassDef(class) = statement else {
                continue;
            };
            if class.name.as_str().starts_with('_') {
                continue;
            }
            let (line, column) = tree.text.position(usize::from(tree.class_start(class)));
            groups
                .entry(package)
                .or_default()
                .entry(class.name.as_str())
                .or_default()
                .push(Definition {
                    source,
                    line,
                    column: column + 1,
                });
        }
    }
    let mut found = Vec::new();
    for (package, names) in &groups {
        for (name, definitions) in names {
            let first = definitions[0].source.module.as_str();
            if definitions.iter().all(|item| item.source.module == first) {
                continue;
            }
            for definition in definitions {
                found.push(finding(package, name, definition, definitions));
            }
        }
    }
    found
}

fn finding(
    package: &str,
    name: &str,
    definition: &Definition<'_>,
    definitions: &[Definition<'_>],
) -> StructureFinding {
    let others: Vec<&Definition<'_>> = definitions
        .iter()
        .filter(|other| other.source.module != definition.source.module)
        .collect();
    let places: Vec<String> = others
        .iter()
        .map(|other| format!("{}:{}", other.source.module, other.line))
        .collect();
    let listed: Vec<_> = others
        .iter()
        .map(|other| json!({"module": other.source.module, "line": other.line}))
        .collect();
    StructureFinding {
        message: format!("Class {name} is also defined in {}", places.join(", ")),
        path: definition.source.path.clone(),
        position: Some((definition.line, definition.column)),
        source_module: Some(definition.source.module.clone()),
        expected: json!({
            "class": name,
            "package": package,
            "others": listed,
            "automatic_rewrite": false,
        }),
        hint: format!(
            "Rename {name} after its role in {}, or merge the classes if they describe the same \
             thing.",
            definition.source.module
        ),
        ..StructureFinding::default()
    }
}
