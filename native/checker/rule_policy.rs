//! MET004: explicitly enabled rules missing the policy they enforce.
use super::config::Request;
use super::config::compose::source_for;
use super::config::paths::read_text;
use super::config::yaml::key_position;
use super::contracts::Contracts;
use super::files::{FileIndex, relative_path};
use super::patterns::is_within;
use super::settings::{CatalogRule, Settings};
use super::violation::Violation;
use serde_json::json;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// The policy each rule enforces; enabling it without the policy checks nothing.
const REQUIRED: [(&str, &str); 4] = [
    ("ARC007", "architecture.packages.<name>.public"),
    (
        "IMP001",
        "architecture.packages.<name>.min_imports_to_bundle",
    ),
    (
        "ARC008",
        "architecture.packages.<name>.min_importers_to_export",
    ),
    ("IMP003", "style.imports"),
];

/// What MET004 reads from the run.
pub struct PolicyInputs<'a> {
    pub catalog: &'a [CatalogRule],
    pub settings: &'a Settings<'a>,
    pub contracts: &'a Contracts<'a>,
    pub files: &'a FileIndex,
    pub request: &'a Request,
    /// The rules a full run with this configuration executes.
    pub configured: &'a HashSet<&'a str>,
}

/// Report each effective named setting whose targets have no matching policy.
pub fn missing_rule_policies(
    inputs: &PolicyInputs<'_>,
    diagnostic: &CatalogRule,
    severity: &str,
) -> Vec<Violation> {
    let mut found = Vec::new();
    for rule in inputs.catalog {
        let Some(required) = REQUIRED
            .iter()
            .find(|(code, _)| *code == rule.code)
            .map(|(_, required)| *required)
        else {
            continue;
        };
        if !inputs.configured.contains(rule.code.as_str())
            || !inputs.settings.has_explicit_activation(&rule.code)
        {
            continue;
        }
        let option = required.rsplit('.').next().unwrap_or(required);
        let covered = covered_modules(inputs, &rule.code, option);
        for (location, source) in uncovered_settings(inputs, &rule.code, &covered) {
            found.push(violation(
                inputs, diagnostic, severity, rule, required, location, source,
            ));
        }
    }
    found
}

fn violation(
    inputs: &PolicyInputs<'_>,
    diagnostic: &CatalogRule,
    severity: &str,
    rule: &CatalogRule,
    required: &str,
    location: &str,
    source: Option<&str>,
) -> Violation {
    let origins = &inputs.request.origins;
    let source = source
        .map(str::to_owned)
        .or_else(|| source_for(origins, &format!("{location}.severity")))
        .or_else(|| source_for(origins, location));
    let path = source.map_or_else(
        || inputs.request.config_path.clone(),
        |source| inputs.files.root.join(source),
    );
    let position = rule_key(&inputs.files.root, &path, location);
    let line = position.map(|(line, _)| line);
    Violation {
        code: diagnostic.code.clone(),
        rule: diagnostic.name.clone(),
        category: diagnostic.category.clone(),
        severity: severity.to_owned(),
        message: format!("{location} enables {} without {required}", rule.name),
        path: relative_path(&inputs.files.root, &path),
        line,
        column: position.map(|(_, column)| column),
        end_line: line,
        end_column: None,
        source_module: None,
        target_module: None,
        import_chain: Vec::new(),
        feature: None,
        layer: None,
        edge: Some(format!("{}:{location}", rule.code)),
        expected: json!({"rule": rule.name, "setting": location, "policy": required}),
        hint: Some(format!(
            "Configure {required} for the intended scope or turn off the rule there."
        )),
        fix: None,
    }
}

/// The modules where the rule finds the policy it enforces.
fn covered_modules<'a>(inputs: &PolicyInputs<'a>, code: &str, option: &str) -> HashSet<&'a str> {
    let modules = inputs.files.sources.keys().map(String::as_str);
    if code == "IMP003" {
        return modules
            .filter(|module| inputs.contracts.policy_for(module, "imports").is_some())
            .collect();
    }
    let packages: Vec<&str> = inputs
        .contracts
        .resolved
        .iter()
        .filter(|contract| match option {
            "public" => contract.spec.public.is_some(),
            "min_imports_to_bundle" => contract.spec.min_imports_to_bundle.is_some(),
            _ => contract.spec.min_importers_to_export.is_some(),
        })
        .map(|contract| contract.package)
        .collect();
    if code == "ARC008" {
        // Export gap findings are anchored at definitions within the contract.
        return modules
            .filter(|module| packages.iter().any(|package| is_within(module, package)))
            .collect();
    }
    // Public and bundling policies constrain outside callers across the project.
    if packages.is_empty() {
        HashSet::new()
    } else {
        modules.collect()
    }
}

/// The enabling settings none of whose modules has the policy, in discovery order.
fn uncovered_settings<'a>(
    inputs: &PolicyInputs<'a>,
    code: &str,
    covered: &HashSet<&str>,
) -> Vec<(&'a str, Option<&'a str>)> {
    let mut origins: Vec<((&str, Option<&str>), bool)> = Vec::new();
    for source in inputs.files.sources.values() {
        let Some(origin) = inputs.settings.explicit_activation(
            code,
            inputs.contracts,
            &source.module,
            &source.path,
        ) else {
            continue;
        };
        let has_policy = covered.contains(source.module.as_str());
        match origins.iter_mut().find(|(known, _)| *known == origin) {
            Some((_, seen)) => *seen |= has_policy,
            None => origins.push((origin, has_policy)),
        }
    }
    origins
        .into_iter()
        .filter(|(_, has_policy)| !has_policy)
        .map(|(origin, _)| origin)
        .collect()
}

/// Where the setting is spelled; a member file spells it without its prefix.
fn rule_key(root: &Path, path: &Path, location: &str) -> Option<(usize, usize)> {
    let text = read_text(path).ok()?;
    let parent = path.parent().map_or_else(PathBuf::new, Path::to_path_buf);
    let local = location
        .strip_prefix(&format!("members.{}.", relative_path(root, &parent)))
        .unwrap_or(location);
    key_position(&text, location).or_else(|| key_position(&text, local))
}
