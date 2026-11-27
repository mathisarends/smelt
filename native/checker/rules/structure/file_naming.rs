//! LAY007: YAML and Compose filenames that differ from the canonical spelling.
use super::{StructureContext, StructureFinding};
use serde_json::{Map, Value, json};

const COMPOSE_NAMES: [&str; 2] = ["compose", "docker-compose"];

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let policy = context.file_naming;
    if !policy.checks_yaml() {
        return Vec::new();
    }
    let mut found = Vec::new();
    for path in context.files.yaml_paths() {
        let (directory, name) = path
            .rsplit_once('/')
            .map_or(("", path.as_str()), |(directory, name)| (directory, name));
        let Some((stem, suffix)) = name.rsplit_once('.') else {
            continue;
        };
        let mut suffix = suffix;
        let mut expected = Map::new();
        let mut spelled = Vec::new();
        if let Some(extension) = policy.yaml_extension.as_deref()
            && suffix != extension
        {
            suffix = extension;
            expected.insert("yaml_extension".to_owned(), json!(extension));
            spelled.push(format!(".{extension}"));
        }
        let (current, variant) = match stem.split_once('.') {
            Some((current, variant)) => (current, Some(variant)),
            None => (stem, None),
        };
        let mut new_stem = stem.to_owned();
        if let Some(basename) = policy.compose_basename.as_deref()
            && COMPOSE_NAMES.contains(&current)
            && current != basename
        {
            new_stem = match variant {
                Some(variant) => format!("{basename}.{variant}"),
                None => basename.to_owned(),
            };
            expected.insert("compose_basename".to_owned(), json!(basename));
            spelled.push(basename.to_owned());
        }
        if expected.is_empty() {
            continue;
        }
        let target = if directory.is_empty() {
            format!("{new_stem}.{suffix}")
        } else {
            format!("{directory}/{new_stem}.{suffix}")
        };
        expected.insert("path".to_owned(), json!(target));
        found.push(StructureFinding {
            message: format!("filename must use {}: {path}", spelled.join(" and ")),
            hint: format!(
                "Rename to \"{target}\" and update filename references; resolve any existing \
                 destination manually."
            ),
            path: path.clone(),
            expected: Value::Object(expected),
            ..StructureFinding::default()
        });
    }
    found
}
