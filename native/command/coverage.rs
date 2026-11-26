//! The report's coverage section, as `smelt.engine.coverage` and
//! `smelt.analysis.contracts.describe_contract` write it.
use super::pyjson::{sorted_ascii, truthy};
use crate::checker::config::load::dump_model;
use crate::checker::config::paths;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::fmt::Write;
use std::path::Path;

const SUMMARY: [&str; 5] = [
    "complete",
    "counts",
    "scan_roots",
    "unexplained_exclusions",
    "discovery_errors",
];
const NON_LAYOUT: [&str; 4] = ["packages", "canonical_exports", "import_graph", "members"];
/// The policy options `describe_contract` lists when a contract sets them.
const DESCRIBED_OPTIONS: [&str; 17] = [
    "module_docstrings",
    "future_annotations",
    "reexports",
    "private_modules",
    "public_attribute_boundary",
    "private_attribute_boundary",
    "max_positional_parameters",
    "max_module_imports",
    "module_import_exemptions",
    "max_comprehension_loops",
    "max_constructor_decisions",
    "max_private_definitions",
    "call_argument_kinds",
    "str_enum_values",
    "dataclass_options",
    "bases",
    "third_party_reexports",
];

fn set(value: &Value) -> bool {
    !value.is_null()
}

/// `ArchitectureConfig.has_layout`: whether a file declares local architecture.
fn has_layout(architecture: &Value) -> bool {
    architecture["__set__"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(|key| !NON_LAYOUT.contains(&key))
}

/// `effective_coverage(config, facts, include_inventory=True)`; `layers` are
/// the run's rule layers.
pub fn effective(config: &Value, layers: &[Value], strict: bool, mut facts: Value) -> Value {
    let mut dumped = dump_model(config);
    use_field_names(&mut dumped);
    // The members that declare their own architecture, with their packages.
    let governing: Map<String, Value> = config["members"]
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(_, member)| has_layout(&member["architecture"]))
        .map(|(path, member)| (path.clone(), member["__packages__"].clone()))
        .collect();
    let has_layout = has_layout(&config["architecture"]) || !governing.is_empty();
    let mut report = Map::new();
    report.insert(
        "mode".into(),
        json!(if strict { "strict" } else { "configured" }),
    );
    report.insert("architecture_configured".into(), json!(has_layout));
    for key in SUMMARY {
        report.insert(key.into(), facts[key].take());
    }
    report.insert("policy_hash".into(), json!(policy_hash(&dumped, layers)));
    report.insert("rules".into(), dumped["rules"].clone());
    let project = &dumped["project"];
    for key in ["root_packages", "source_roots", "test_roots"] {
        report.insert(key.into(), project[key].clone());
    }
    let exclusions = project["exclude"].as_array().cloned().unwrap_or_default();
    report.insert(
        "exclude".into(),
        Value::Array(
            exclusions
                .iter()
                .map(|entry| match entry {
                    Value::String(_) => entry.clone(),
                    other => other["path"].clone(),
                })
                .collect(),
        ),
    );
    report.insert("exclusions".into(), Value::Array(exclusions));
    report.insert(
        "omitted_workspace_members".into(),
        facts["omitted_workspace_members"].take(),
    );
    report.insert("member_architectures".into(), Value::Object(governing));
    report.insert(
        "member_boundaries".into(),
        match &dumped["architecture"]["members"] {
            Value::Object(boundaries) => Value::Object(boundaries.clone()),
            _ => json!({}),
        },
    );
    report.insert("modules".into(), facts["modules"].take());
    let architecture = &dumped["architecture"];
    report.insert(
        "exempt".into(),
        json!({
            "composition_root": architecture["composition_root"],
            "wiring": architecture["wiring"],
        }),
    );
    let mut third_party = Map::new();
    for (name, layer) in architecture["layers"].as_object().into_iter().flatten() {
        third_party.insert(name.clone(), layer["third_party"].clone());
    }
    report.insert("third_party".into(), Value::Object(third_party));
    report.insert("imports".into(), architecture["import_graph"].clone());
    let cross_feature = &architecture["cross_feature"];
    report.insert(
        "cross_feature".into(),
        json!({
            "default": cross_feature["default"],
            "allow": cross_feature["allow"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(allowance)
                .collect::<Vec<_>>(),
        }),
    );
    let contracts = &config["__structure__"]["contracts"];
    report.insert(
        "contracts".into(),
        Value::Array(
            facts["contracts"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|entry| describe(entry, &contracts[entry["name"].as_str().unwrap_or("")]))
                .collect(),
        ),
    );
    report.insert("python_targets".into(), facts["python_targets"].take());
    report.insert("tests".into(), dumped["tests"].clone());
    report.insert("inventory".into(), facts["inventory"].take());
    if config["project"]["workspace"] == "uv" {
        report.insert("scope".into(), json!("workspace"));
    }
    if let Some(ordering) = facts.get_mut("ordering") {
        report.insert("ordering".into(), ordering.take());
    }
    if let Some(Value::Object(quality)) = facts.get_mut("test_quality") {
        let mut described = Map::new();
        described.insert("mode".into(), json!("syntactic"));
        for (key, value) in std::mem::take(quality) {
            described.insert(key, value);
        }
        described.insert("fixture_traversal".into(), json!(false));
        described.insert("fixture_return_annotations".into(), json!(true));
        described.insert(
            "fixture_return_inference".into(),
            json!("consistent known constructor/alias or fixed tuple; no execution"),
        );
        described.insert(
            "operation_families".into(),
            json!([
                "unittest.mock",
                "pytest-mock",
                "pytest.MonkeyPatch.setattr/delattr"
            ]),
        );
        described.insert(
            "instance_provenance".into(),
            json!([
                "constructor",
                "annotation",
                "fixture-return",
                "fixture-inferred"
            ]),
        );
        report.insert("test_quality".into(), Value::Object(described));
    }
    Value::Object(report)
}

/// `CrossFeatureConfig.entries`: an allowance as fields; a `null` feature
/// stands for every feature.
fn allowance(entry: &Value) -> Option<Value> {
    if let Some(text) = entry.as_str() {
        let (source, target) = layer_pair(text)?;
        return Some(json!({
            "source_feature": null,
            "source_layer": source,
            "target_feature": null,
            "target_layer": target,
        }));
    }
    let (source_feature, source_layer) = entry["source"].as_str()?.split_once('.')?;
    let (target_feature, target_layer) = entry["target"].as_str()?.split_once('.')?;
    Some(json!({
        "source_feature": source_feature,
        "source_layer": source_layer,
        "target_feature": target_feature,
        "target_layer": target_layer,
    }))
}

/// `_LAYER_PAIR`: `source -> target` between two layer names.
fn layer_pair(text: &str) -> Option<(&str, &str)> {
    let (source, target) = text.split_once("->")?;
    let (source, target) = (trim_space(source), trim_space(target));
    (layer_name(source) && layer_name(target)).then_some((source, target))
}

fn trim_space(text: &str) -> &str {
    text.trim_matches(super::pytext::is_space)
}

fn layer_name(name: &str) -> bool {
    let mut characters = name.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
        && characters
            .all(|character| character.is_alphanumeric() || character == '_' || character == '.')
}

/// `policy_hash`: equal for two reports checked under the same resolved config.
fn policy_hash(dumped: &Value, layers: &[Value]) -> String {
    let mut policy = dumped.clone();
    if let Some(entries) = policy.as_object_mut() {
        entries.shift_remove("plugins"); // Plugin checks use the Python policy loader.
    }
    if let Some(entries) = policy.as_object_mut()
        && !entries.get("members").is_some_and(truthy)
    {
        entries.shift_remove("members");
    }
    for member in policy
        .get_mut("members")
        .and_then(Value::as_object_mut)
        .into_iter()
        .flat_map(|members| members.values_mut())
    {
        if let Some(architecture) = member.get_mut("architecture") {
            without_empty_modules(architecture);
        }
    }
    let architecture = policy["architecture"].as_object_mut();
    if let Some(architecture) = architecture {
        if !architecture.get("canonical_exports").is_some_and(truthy) {
            architecture.shift_remove("canonical_exports");
        }
        if let Some(graph) = architecture
            .get_mut("import_graph")
            .and_then(Value::as_object_mut)
            && graph.get("type_checking_cycles").and_then(Value::as_str) == Some("include")
        {
            graph.shift_remove("type_checking_cycles");
        }
        if !architecture.get("members").is_some_and(truthy) {
            architecture.shift_remove("members");
        }
    }
    without_empty_modules(&mut policy["architecture"]);
    if let Some(project) = policy["project"].as_object_mut()
        && project.get("workspace").is_none_or(Value::is_null)
    {
        project.shift_remove("workspace");
    }
    if layers.len() > 1 {
        policy["rule_layers"] = Value::Array(layers.to_vec());
    }
    let digest = Sha256::digest(sorted_ascii(&policy).as_bytes());
    let mut hash = String::from("sha256:");
    for byte in &digest[..8] {
        let _ = write!(hash, "{byte:02x}");
    }
    hash
}

/// Drop the empty `modules` of an architecture's layers.
fn without_empty_modules(architecture: &mut Value) {
    for layer in architecture
        .get_mut("layers")
        .and_then(Value::as_object_mut)
        .into_iter()
        .flat_map(|layers| layers.values_mut())
    {
        if let Some(layer) = layer.as_object_mut()
            && !layer.get("modules").is_some_and(truthy)
        {
            layer.shift_remove("modules");
        }
    }
}

/// The field names of a dump without aliases, as `model_dump()` writes them:
/// `OrderingConfig.class_order` and a `{from, to}` allowance's `source` and
/// `target`.
fn use_field_names(value: &mut Value) {
    match value {
        Value::Object(entries) => {
            if let Some(Value::Object(ordering)) = entries.get_mut("ordering") {
                rename(ordering, "class", "class_order");
            }
            let allowances = entries
                .get_mut("cross_feature")
                .and_then(|cross_feature| cross_feature.get_mut("allow"))
                .and_then(Value::as_array_mut);
            for allowance in allowances.into_iter().flatten() {
                if let Some(allowance) = allowance.as_object_mut() {
                    rename(allowance, "from", "source");
                    rename(allowance, "to", "target");
                }
            }
            entries.values_mut().for_each(use_field_names);
        }
        Value::Array(items) => items.iter_mut().for_each(use_field_names),
        _ => {}
    }
}

fn rename(entries: &mut Map<String, Value>, alias: &str, field: &str) {
    if let Some(value) = entries.shift_remove(alias) {
        entries.insert(field.to_owned(), value);
    }
}

/// `describe_contract`: a resolved contract's policy and what it lacks.
fn describe(entry: &Value, spec: &Value) -> Value {
    let package = entry["package"].as_str().unwrap_or_default();
    let qualify = |relative: &Value| {
        let relative = relative.as_str().unwrap_or_default();
        json!(if relative == "." {
            package.to_owned()
        } else {
            format!("{package}.{relative}")
        })
    };
    let mut described = Map::new();
    described.insert("name".into(), entry["name"].clone());
    described.insert("package".into(), json!(package));
    described.insert("match".into(), spec["match"].clone());
    described.insert("rules".into(), spec["rules"].clone());
    described.insert(
        "required".into(),
        Value::Array(
            spec["required"]
                .as_array()
                .into_iter()
                .flatten()
                .map(qualify)
                .collect(),
        ),
    );
    described.insert("missing".into(), entry["missing"].clone());
    described.insert(
        "public".into(),
        match spec["public"].as_array() {
            Some(public) => Value::Array(public.iter().map(qualify).collect()),
            None => Value::Null,
        },
    );
    described.insert("missing_public".into(), entry["missing_public"].clone());
    if spec["test_access"] != "public" {
        described.insert("test_access".into(), spec["test_access"].clone());
    }
    for key in ["type_dispatch", "errors", "navigation", "ordering"] {
        if set(&spec[key]) {
            described.insert(key.into(), spec[key].clone());
        }
    }
    described.insert("target_public".into(), Value::Null);
    for key in ["imports", "min_imports_to_bundle"] {
        if set(&spec[key]) {
            described.insert(key.into(), spec[key].clone());
        }
    }
    if set(&spec["min_importers_to_export"]) {
        described.insert(
            "min_importers_to_export".into(),
            spec["min_importers_to_export"].clone(),
        );
        described.insert("count_tests".into(), spec["count_tests"].clone());
    }
    for key in DESCRIBED_OPTIONS {
        if set(&spec[key]) {
            described.insert(key.into(), spec[key].clone());
        }
    }
    Value::Object(described)
}

/// `project_metadata`: where config-relative paths start, and their prefix in
/// the repository.
pub fn project_metadata(config_path: &Path) -> Value {
    let config = paths::resolve(config_path);
    let root = paths::resolve(config_path.parent().unwrap_or(config_path));
    let repository = root
        .ancestors()
        .find(|directory| directory.join(".git").exists())
        .unwrap_or(&root);
    let prefix = root
        .strip_prefix(repository)
        .map(paths::posix)
        .unwrap_or_default();
    json!({
        "root": paths::posix(&root),
        "config": paths::posix(&config),
        "repository_root": paths::posix(repository),
        "repository_prefix": prefix,
    })
}
