//! Rule activation and severity as `smelt.engine.rule_settings.RuleSettings`
//! resolves them for a project without workspace members.
//!
//! Every configuration the Python settings would reject returns `Err(())`, so
//! the Python CLI reports it instead.
use super::finding::Severity;
use serde::Deserialize;
use serde_json::Value;
use std::collections::{BTreeSet, HashMap, HashSet};

pub const STRICT_COVERAGE: &str = "ARC013";
const ALL_RULES: &str = "ALL";
const CATEGORIES: [&str; 11] = [
    "architecture",
    "imports",
    "layout",
    "visibility",
    "modeling",
    "typing",
    "complexity",
    "idioms",
    "frameworks",
    "tests",
    "meta",
];
/// The limit option and the rule code of each rule that takes a `max`.
const LIMIT_RULES: [(&str, &str, &str); 7] = [
    (
        "max_module_imports",
        "IMP002",
        "imports/excess-module-imports",
    ),
    (
        "max_positional_parameters",
        "CPX003",
        "complexity/excess-positional-parameters",
    ),
    (
        "max_comprehension_loops",
        "CPX006",
        "complexity/excess-comprehension-loops",
    ),
    (
        "max_constructor_decisions",
        "CPX007",
        "complexity/excess-constructor-decisions",
    ),
    (
        "max_private_definitions",
        "VIS007",
        "visibility/excess-private-definitions",
    ),
    ("max_patches_per_test", "TST005", "tests/excess-patches"),
    (
        "max_uncovered_facade_callers",
        "MET005",
        "meta/uncovered-package-facade",
    ),
];
/// Project-wide limits that package and member entries cannot refine.
const ROOT_LIMITS: [&str; 2] = ["TST005", "MET005"];

/// One catalog entry of `smelt/rules/catalog.json`.
#[derive(Deserialize)]
pub struct Rule {
    pub code: String,
    pub name: String,
    pub category: String,
    pub default_severity: String,
    pub enabled_by_default: bool,
    pub scope: String,
    #[serde(default)]
    pub summary: String,
}

impl Rule {
    pub fn default_severity(&self) -> Severity {
        Severity::parse(&self.default_severity).unwrap_or(Severity::Error)
    }
}

pub struct Catalog {
    pub rules: Vec<Rule>,
    /// Each code and rule name to its code.
    codes: HashMap<String, String>,
}

impl Catalog {
    pub fn load(text: &str) -> Option<Self> {
        let rules: Vec<Rule> = serde_json::from_str(text).ok()?;
        let mut codes = HashMap::new();
        for rule in &rules {
            codes.insert(rule.code.clone(), rule.code.clone());
        }
        for rule in &rules {
            codes.insert(rule.name.clone(), rule.code.clone());
        }
        Some(Self { rules, codes })
    }

    pub fn by_code(&self, code: &str) -> Option<&Rule> {
        self.rules.iter().find(|rule| rule.code == code)
    }

    /// `RuleSet.family_codes`: the codes of `modeling` or `modeling/`.
    pub fn family_codes(&self, selector: &str) -> Option<Vec<&str>> {
        let lowered = selector.trim().to_lowercase();
        let wanted = lowered.strip_suffix('/').unwrap_or(&lowered);
        if !CATEGORIES.contains(&wanted) {
            return None;
        }
        Some(
            self.rules
                .iter()
                .filter(|rule| rule.category == wanted)
                .map(|rule| rule.code.as_str())
                .collect(),
        )
    }

    /// `RuleSet.matching_codes`: a name, family, code prefix or ALL.
    pub fn matching_codes(&self, selector: &str) -> Vec<&str> {
        let wanted = selector.trim();
        if let Some(family) = self.family_codes(wanted) {
            return family;
        }
        let lowered = wanted.to_lowercase();
        let lookup = lowered.trim();
        if let Some(rule) = self
            .rules
            .iter()
            .find(|rule| lookup.to_uppercase() == rule.code || lookup == rule.name)
        {
            return vec![rule.code.as_str()];
        }
        let prefix = wanted.to_uppercase();
        self.rules
            .iter()
            .filter(|rule| {
                !prefix.is_empty() && (prefix == ALL_RULES || rule.code.starts_with(&prefix))
            })
            .map(|rule| rule.code.as_str())
            .collect()
    }

    /// `ALL` or a family, with its breadth rank.
    fn broad_selection(&self, key: &str) -> Option<(u8, Vec<&str>)> {
        if key == ALL_RULES {
            return Some((
                0,
                self.rules.iter().map(|rule| rule.code.as_str()).collect(),
            ));
        }
        self.family_codes(key).map(|codes| (1, codes))
    }

    fn code_of(&self, key: &str) -> Option<&str> {
        self.codes.get(key).map(String::as_str)
    }
}

/// The severity an entry sets; `None` when it only sets the rule's max.
fn raw_value(setting: &Value) -> Option<&str> {
    match setting {
        Value::String(value) => Some(value),
        Value::Object(entry) => entry.get("severity").and_then(Value::as_str),
        _ => None,
    }
}

fn entry_max(setting: &Value) -> Option<&Value> {
    setting.get("max").filter(|value| !value.is_null())
}

#[derive(Clone)]
struct Setting {
    value: String,
    broad: bool,
}

impl Setting {
    fn enables(&self) -> bool {
        !self.broad && self.value != "off"
    }

    fn apply(&self, inherited: Option<String>) -> Option<String> {
        if !self.broad || self.value == "off" {
            return Some(self.value.clone());
        }
        let inherited = inherited?;
        Some(if rank(&self.value) < rank(&inherited) {
            self.value.clone()
        } else {
            inherited
        })
    }
}

fn rank(value: &str) -> u8 {
    match value {
        "hint" => 1,
        "warning" => 2,
        "error" => 3,
        _ => 0,
    }
}

type Entries = HashMap<String, Setting>;

/// A resolved contract: its name and the package it matched.
pub struct Resolved {
    pub name: String,
    pub package: String,
}

pub struct Policy<'a> {
    catalog: &'a Catalog,
    config: &'a Value,
    layers: Vec<Entries>,
    /// The rules of each package contract, in contract order.
    packages: Vec<(String, Entries)>,
    strict: bool,
}

impl<'a> Policy<'a> {
    /// The settings of `config`, whose rule layers come base first.
    pub fn new(catalog: &'a Catalog, config: &'a Value, layers: &[Value]) -> Result<Self, ()> {
        let strict = strict_coverage(layers);
        let guard: HashSet<&str> = if strict {
            HashSet::from([STRICT_COVERAGE])
        } else {
            HashSet::new()
        };
        let project_rules: HashSet<&str> = catalog
            .rules
            .iter()
            .filter(|rule| rule.scope != "source")
            .map(|rule| rule.code.as_str())
            .collect();
        let mut issues = 0usize;
        let mut resolved_layers = Vec::new();
        for layer in layers {
            resolved_layers.push(entries(catalog, layer, &guard, &mut issues));
        }
        let contracts = config["__structure__"]["contracts"].as_object();
        let mut packages = Vec::new();
        for (name, contract) in contracts.into_iter().flatten() {
            packages.push((
                name.clone(),
                entries(catalog, &contract["rules"], &project_rules, &mut issues),
            ));
        }
        let mut policy = Self {
            catalog,
            config,
            layers: resolved_layers,
            packages,
            strict,
        };
        issues += policy.root_limit_issues(layers);
        for (_, override_) in config["style"]["overrides"]
            .as_object()
            .into_iter()
            .flatten()
        {
            for (key, value) in override_["rules"].as_object().into_iter().flatten() {
                if catalog
                    .code_of(key)
                    .is_some_and(|code| ROOT_LIMITS.contains(&code))
                    && value.is_object()
                    && entry_max(value).is_some()
                {
                    issues += 1;
                }
            }
        }
        issues += policy.root_broad_issues(layers);
        for entry in config["ignore"].as_array().into_iter().flatten() {
            let rule = entry["rule"].as_str().unwrap_or_default();
            if catalog.matching_codes(rule).is_empty() {
                issues += 1;
            }
        }
        if issues > 0 || policy.policy_issues() {
            return Err(());
        }
        let guards: Vec<&Setting> = policy
            .layers
            .iter()
            .filter_map(|layer| layer.get(STRICT_COVERAGE))
            .collect();
        if strict && guards.last().is_none_or(|setting| setting.value != "error") {
            return Err(());
        }
        if policy.packages.iter().any(|(_, settings)| {
            settings
                .keys()
                .any(|code| project_rules.contains(code.as_str()))
        }) {
            return Err(());
        }
        policy.layers.shrink_to_fit();
        Ok(policy)
    }

    fn default_value(&self, rule: &Rule) -> Option<String> {
        rule.enabled_by_default
            .then(|| rule.default_severity.clone())
    }

    fn apply_layers(&self, code: &str, mut value: Option<String>, upto: usize) -> Option<String> {
        for layer in &self.layers[..upto.min(self.layers.len())] {
            if let Some(setting) = layer.get(code) {
                value = setting.apply(value);
            }
        }
        value
    }

    pub fn global_value(&self, rule: &Rule) -> Option<String> {
        self.apply_layers(&rule.code, self.default_value(rule), usize::MAX)
    }

    pub fn has_package_activation(&self, code: &str) -> bool {
        self.packages
            .iter()
            .any(|(_, settings)| settings.get(code).is_some_and(Setting::enables))
    }

    /// `_root_limit_issues`: a root entry that sets only max leaves its rule off.
    fn root_limit_issues(&self, layers: &[Value]) -> usize {
        let named: HashSet<&String> = self.layers.iter().flat_map(HashMap::keys).collect();
        layers
            .iter()
            .flat_map(|layer| layer.as_object().into_iter().flatten())
            .filter(|(key, value)| {
                value.is_object()
                    && raw_value(value).is_none()
                    && self
                        .catalog
                        .code_of(key)
                        .is_some_and(|code| !named.contains(&code.to_owned()))
            })
            .count()
    }

    /// `_validate_root_broad`: a broad selector must cap a rule enabled before it.
    fn root_broad_issues(&self, layers: &[Value]) -> usize {
        let scoped: HashSet<&str> = self
            .packages
            .iter()
            .flat_map(|(_, entries)| entries.iter())
            .filter(|(_, setting)| !setting.broad && setting.value != "off")
            .map(|(code, _)| code.as_str())
            .collect();
        let mut issues = 0;
        for (index, layer) in layers.iter().enumerate() {
            let mut active: HashSet<&str> = scoped.clone();
            for rule in &self.catalog.rules {
                if self
                    .apply_layers(&rule.code, self.default_value(rule), index)
                    .is_some_and(|value| value != "off")
                {
                    active.insert(&rule.code);
                }
            }
            issues += self.broad_issues(layer, &self.layers[index], active);
        }
        issues
    }

    fn broad_issues<'s>(
        &'s self,
        raw: &Value,
        entries: &'s Entries,
        mut active: HashSet<&'s str>,
    ) -> usize {
        let broad: Vec<(&String, Vec<&str>)> = raw
            .as_object()
            .into_iter()
            .flatten()
            .filter_map(|(key, _)| {
                self.catalog
                    .broad_selection(key)
                    .map(|(_, codes)| (key, codes))
            })
            .collect();
        if broad.is_empty() {
            return 0;
        }
        active.extend(
            entries
                .iter()
                .filter(|(_, setting)| !setting.broad && setting.value != "off")
                .map(|(code, _)| code.as_str()),
        );
        broad
            .iter()
            .filter(|(key, codes)| {
                raw_value(&raw[key.as_str()]) != Some("off")
                    && !codes.iter().any(|code| active.contains(code))
            })
            .count()
    }

    /// `_validate_policies`: an enabled rule whose required policy is missing.
    fn policy_issues(&self) -> bool {
        self.catalog.rules.iter().any(|rule| {
            let Some(activation) = activation(&rule.code) else {
                return false;
            };
            if !activation.required {
                return false;
            }
            let enabled = self
                .layers
                .iter()
                .chain(self.packages.iter().map(|(_, entries)| entries))
                .any(|layer| layer.get(&rule.code).is_some_and(Setting::enables));
            enabled && !(activation.matches)(self.config)
        })
    }

    /// The codes `--select` or `--ignore` prefixes name; `Err` for an unknown one.
    pub fn selected_codes(&self, prefixes: &[String]) -> Result<Vec<&'a str>, ()> {
        let mut codes = Vec::new();
        for prefix in prefixes {
            let matches = self.catalog.matching_codes(prefix);
            if matches.is_empty() {
                return Err(());
            }
            codes.extend(matches);
        }
        Ok(codes)
    }

    /// `_resolve_active_rules`: each active rule with its severity.
    pub fn active(
        &self,
        select: &[&str],
        ignore: &[&str],
    ) -> Result<Vec<(&'a Rule, Severity)>, ()> {
        let matches = |code: &str, prefixes: &[&str]| {
            prefixes.iter().any(|prefix| {
                let wanted = prefix.trim().to_uppercase();
                !prefix.is_empty() && (wanted == ALL_RULES || code.starts_with(&wanted))
            })
        };
        let mut active = Vec::new();
        for rule in &self.catalog.rules {
            let setting = self.global_value(rule);
            if rule.code == STRICT_COVERAGE && self.strict {
                if setting.as_deref().is_some_and(|value| value != "error")
                    || matches(&rule.code, ignore)
                {
                    return Err(());
                }
                active.push((rule, Severity::Error));
                continue;
            }
            let selected = !select.is_empty() && matches(&rule.code, select);
            let package_enabled = self.has_package_activation(&rule.code);
            if setting.as_deref() == Some("off") && !package_enabled {
                continue;
            }
            let severity = match setting.as_deref() {
                Some(value) if value != "off" => Severity::parse(value).ok_or(())?,
                _ if selected || package_enabled => rule.default_severity(),
                _ => continue,
            };
            if !select.is_empty() && !matches(&rule.code, select) {
                continue;
            }
            if !ignore.is_empty() && matches(&rule.code, ignore) {
                continue;
            }
            active.push((rule, severity));
        }
        Ok(active)
    }

    /// `validate_packages` without workspace members: `Err` where it would raise.
    pub fn validate_packages(&self, resolved: &[Resolved]) -> Result<(), ()> {
        let mut seen: HashMap<(&str, &str), (&str, bool)> = HashMap::new();
        for contract in resolved {
            let Some(settings) = self.package(&contract.name) else {
                continue;
            };
            for (code, setting) in settings {
                let value = (
                    setting.value.as_str(),
                    setting.broad && setting.value != "off",
                );
                let previous = *seen
                    .entry((contract.package.as_str(), code.as_str()))
                    .or_insert(value);
                if previous != value {
                    return Err(());
                }
            }
        }
        // `_validate_scoped_selectors`: a package's broad selector must cap a rule.
        let contracts = self.config["__structure__"]["contracts"].as_object();
        for (name, contract) in contracts.into_iter().flatten() {
            let raw = &contract["rules"];
            if !raw
                .as_object()
                .into_iter()
                .flatten()
                .any(|(key, _)| self.catalog.broad_selection(key).is_some())
            {
                continue;
            }
            let packages: Vec<&str> = resolved
                .iter()
                .filter(|resolved| resolved.name == *name)
                .map(|resolved| resolved.package.as_str())
                .collect();
            let active: HashSet<&str> = self
                .catalog
                .rules
                .iter()
                .filter(|rule| {
                    packages.iter().any(|package| {
                        self.severity(rule, resolved, Some(package), false)
                            .is_some()
                    })
                })
                .map(|rule| rule.code.as_str())
                .collect();
            let Some(entries) = self.package(name) else {
                continue;
            };
            if self.broad_issues(raw, entries, active) > 0 {
                return Err(());
            }
        }
        Ok(())
    }

    fn package(&self, name: &str) -> Option<&Entries> {
        self.packages
            .iter()
            .find(|(package, _)| package == name)
            .map(|(_, entries)| entries)
    }

    /// `decision(...).severity`: the severity of `rule` in `module`.
    pub fn severity(
        &self,
        rule: &Rule,
        resolved: &[Resolved],
        module: Option<&str>,
        selected: bool,
    ) -> Option<Severity> {
        let mut value = self.default_value(rule);
        for layer in &self.layers {
            if let Some(setting) = layer.get(&rule.code) {
                value = setting.apply(value);
            }
        }
        if let Some(module) = module.filter(|module| !module.is_empty()) {
            let mut candidates: Vec<&Resolved> = resolved
                .iter()
                .filter(|contract| {
                    module == contract.package
                        || module
                            .strip_prefix(contract.package.as_str())
                            .is_some_and(|rest| rest.starts_with('.'))
                })
                .collect();
            candidates.sort_by_key(|contract| contract.package.len());
            for contract in candidates {
                if let Some(setting) = self
                    .package(&contract.name)
                    .and_then(|entries| entries.get(&rule.code))
                {
                    value = setting.apply(value);
                }
            }
        }
        if value.is_none() && selected {
            value = Some(rule.default_severity.clone());
        }
        value
            .filter(|value| value != "off")
            .and_then(|value| Severity::parse(&value))
    }

    /// `unused_policies`: declared policies whose rule no entry names.
    pub fn unused_policies(&self) -> Vec<(String, String)> {
        let named: HashSet<&str> = self
            .layers
            .iter()
            .chain(self.packages.iter().map(|(_, entries)| entries))
            .flat_map(|layer| layer.iter())
            .filter(|(_, setting)| !setting.broad || setting.value == "off")
            .map(|(code, _)| code.as_str())
            .collect();
        let mut issues = Vec::new();
        for rule in &self.catalog.rules {
            let Some(activation) = activation(&rule.code) else {
                continue;
            };
            if rule.enabled_by_default || !(activation.matches)(self.config) {
                continue;
            }
            let root_declares = match activation.option {
                None => (activation.matches)(self.config),
                Some(option) => (activation.accept)(&self.config["__structure__"][option]),
            };
            if named.contains(rule.code.as_str())
                && (!root_declares || self.global_value(rule).is_some())
            {
                continue;
            }
            let description = activation.description.trim_end_matches('.');
            issues.push((
                format!("rules.{}", rule.name),
                format!(
                    "{description}, but {name} is not enabled; add \"{name}: {severity}\" under rules, or \"off\" to keep the policy unchecked",
                    name = rule.name,
                    severity = rule.default_severity,
                ),
            ));
        }
        issues
    }
}

/// Whether the rule layers enable architecture/uncovered-source.
pub fn strict_coverage(layers: &[Value]) -> bool {
    let mut setting = None;
    for layer in layers {
        for key in [STRICT_COVERAGE, "architecture/uncovered-source"] {
            if let Some(value) = layer.get(key) {
                setting = Some(value);
            }
        }
    }
    setting
        .and_then(raw_value)
        .is_some_and(|value| value != "off")
}

/// `_entries`: selectors resolved to their winning settings; each issue the
/// Python settings would report is counted.
fn entries(
    catalog: &Catalog,
    raw: &Value,
    excluded: &HashSet<&str>,
    issues: &mut usize,
) -> Entries {
    let mut result = Entries::new();
    let Some(raw) = raw.as_object() else {
        return result;
    };
    for (key, value) in raw {
        let Some(max) = entry_max(value).filter(|_| value.is_object()) else {
            continue;
        };
        if catalog.broad_selection(key).is_some() {
            *issues += 1;
        } else if let Some(code) = catalog.code_of(key) {
            match LIMIT_RULES.iter().find(|(_, limit, _)| *limit == code) {
                None => *issues += 1,
                Some(_) => {
                    let minimum = if code == "CPX006" { 1 } else { 0 };
                    if max.as_i64().is_none_or(|max| max < minimum) {
                        *issues += 1;
                    }
                }
            }
        } else if raw_value(value).is_none() {
            *issues += 1;
        }
    }
    let set: Vec<(&String, &str)> = raw
        .iter()
        .filter_map(|(key, value)| {
            raw_value(value)
                .filter(|value| !value.is_empty())
                .map(|value| (key, value))
        })
        .collect();
    let mut broad: Vec<(u8, &String, Vec<&str>, &str)> = set
        .iter()
        .filter_map(|(key, value)| {
            catalog
                .broad_selection(key)
                .map(|(level, codes)| (level, *key, codes, *value))
        })
        .collect();
    broad.sort_by(|left, right| (left.0, left.1).cmp(&(right.0, right.1)));
    for (_, _, codes, value) in broad {
        if value == "on" {
            *issues += 1;
            continue;
        }
        for code in codes {
            if !excluded.contains(code) {
                result.insert(
                    code.to_owned(),
                    Setting {
                        value: value.to_owned(),
                        broad: true,
                    },
                );
            }
        }
    }
    let mut individual: BTreeSet<&str> = BTreeSet::new();
    for (key, value) in set {
        if catalog.broad_selection(key).is_some() {
            continue;
        }
        match catalog.code_of(key) {
            None => *issues += 1,
            Some(code) if individual.contains(code) => *issues += 1,
            Some(code) => {
                individual.insert(code);
                let severity = if value == "on" {
                    catalog
                        .by_code(code)
                        .map_or("error", |rule| rule.default_severity.as_str())
                } else {
                    value
                };
                result.insert(
                    code.to_owned(),
                    Setting {
                        value: severity.to_owned(),
                        broad: false,
                    },
                );
            }
        }
    }
    result
}

/// `PolicyActivation` from `smelt.rules.activation.POLICIES`.
pub struct Activation {
    pub description: &'static str,
    pub matches: fn(&Value) -> bool,
    pub option: Option<&'static str>,
    pub required: bool,
    pub accept: fn(&Value) -> bool,
}

fn is_set(value: &Value) -> bool {
    !value.is_null()
}

pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|number| number != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(entries) => !entries.is_empty(),
    }
}

/// The policies an option-based activation reads: style, then every contract.
fn policies(config: &Value) -> impl Iterator<Item = &Value> {
    let structure = &config["__structure__"];
    std::iter::once(structure).chain(
        structure["contracts"]
            .as_object()
            .into_iter()
            .flat_map(|contracts| contracts.values()),
    )
}

fn contracts(config: &Value) -> impl Iterator<Item = &Value> {
    config["__structure__"]["contracts"]
        .as_object()
        .into_iter()
        .flat_map(|contracts| contracts.values())
}

macro_rules! option_activation {
    ($description:expr, $option:expr, $required:expr, $accept:expr) => {
        Activation {
            description: $description,
            matches: |config| policies(config).any(|policy| ($accept)(&policy[$option])),
            option: Some($option),
            required: $required,
            accept: $accept,
        }
    };
}

macro_rules! activation {
    ($description:expr, $matches:expr) => {
        Activation {
            description: $description,
            matches: $matches,
            option: None,
            required: true,
            accept: is_set,
        }
    };
}

fn max_patches_per_test(config: &Value) -> bool {
    let rules = &config["rules"];
    ["TST005", "tests/excess-patches"]
        .iter()
        .any(|key| rules[*key].is_object() && entry_max(&rules[*key]).is_some())
}

pub fn activation(code: &str) -> Option<Activation> {
    Some(match code {
        "LAY007" => activation!(
            "style.file_naming selects a canonical YAML extension or Compose basename.",
            |config| {
                let naming = &config["__structure__"]["file_naming"];
                is_set(&naming["yaml_extension"]) || is_set(&naming["compose_basename"])
            }
        ),
        "ARC001" => activation!(
            "architecture.layers defines layer dependencies.",
            |config| { truthy(&config["architecture"]["layers"]) }
        ),
        "ARC002" => activation!(
            "architecture.features defines feature boundaries.",
            |config| { is_set(&config["architecture"]["features"]) }
        ),
        "ARC003" => activation!(
            "A layer's third_party policy restricts dependencies.",
            |config| {
                config["architecture"]["layers"]
                    .as_object()
                    .into_iter()
                    .flatten()
                    .any(|(_, layer)| {
                        layer["third_party"]["default"] == "deny"
                            || truthy(&layer["third_party"]["deny"])
                    })
            }
        ),
        "ARC005" => activation!(
            "architecture.shared or a layer module binding declares shared/central modules.",
            |config| {
                let architecture = &config["architecture"];
                truthy(&architecture["shared"])
                    || architecture["layers"]
                        .as_object()
                        .into_iter()
                        .flatten()
                        .any(|(_, layer)| truthy(&layer["modules"]))
            }
        ),
        "ARC006" => activation!(
            "architecture.composition_root or architecture.wiring declares assembly boundaries.",
            |config| {
                truthy(&config["architecture"]["composition_root"])
                    || truthy(&config["architecture"]["wiring"])
            }
        ),
        "ARC007" => activation!(
            "A package API declares public entry points, including an explicitly empty public list.",
            |config| contracts(config).any(|contract| is_set(&contract["public"]))
        ),
        "IMP001" => activation!("A package API sets min_imports_to_bundle.", |config| {
            contracts(config).any(|contract| is_set(&contract["min_imports_to_bundle"]))
        }),
        "ARC008" => activation!("A package API sets min_importers_to_export.", |config| {
            contracts(config).any(|contract| is_set(&contract["min_importers_to_export"]))
        }),
        "IMP002" => option_activation!(
            "style or a package override sets module_import_exemptions.",
            "module_import_exemptions",
            false,
            is_set
        ),
        "ARC009" => activation!(
            "architecture.members declares workspace member boundaries.",
            |config| truthy(&config["architecture"]["members"])
        ),
        "ARC010" => activation!(
            "project.workspace is uv; static member metadata declares dependencies.",
            |config| config["project"]["workspace"] == "uv"
        ),
        "LAY001" => activation!(
            "style.forbidden_names contains naming restrictions.",
            |config| { truthy(&config["__structure__"]["forbidden_names"]) }
        ),
        "ARC014" => activation!(
            "A package API requires modules or named public entry points.",
            |config| contracts(config)
                .any(|contract| truthy(&contract["required"]) || truthy(&contract["public"]))
        ),
        "IMP003" => option_activation!(
            "style or a package override sets imports.",
            "imports",
            true,
            is_set
        ),
        "LAY002" => option_activation!(
            "style or a package override sets module_docstrings to require or forbid.",
            "module_docstrings",
            true,
            |value: &Value| value == "forbid" || value == "require"
        ),
        "TYP001" => option_activation!(
            "style or a package override sets future_annotations to forbid or when-needed.",
            "future_annotations",
            true,
            |value: &Value| value == "forbid" || value == "when-needed"
        ),
        "TYP002" => option_activation!(
            "style or a package override sets future_annotations to when-needed.",
            "future_annotations",
            true,
            |value: &Value| value == "when-needed"
        ),
        "LAY003" => option_activation!(
            "style or a package override sets private_modules to forbid.",
            "private_modules",
            true,
            |value: &Value| value == "forbid"
        ),
        "IDM002" => option_activation!(
            "style or a package override sets type_dispatch.style to match.",
            "type_dispatch",
            true,
            |value: &Value| !value.is_null() && value["style"] == "match"
        ),
        "VIS004" => option_activation!(
            "style or a package override sets public_attribute_boundary.",
            "public_attribute_boundary",
            false,
            is_set
        ),
        "VIS005" => option_activation!(
            "style or a package override sets private_attribute_boundary.",
            "private_attribute_boundary",
            false,
            is_set
        ),
        "CPX001" => option_activation!(
            "style or a package override sets navigation.",
            "navigation",
            false,
            is_set
        ),
        "CPX002" => option_activation!(
            "style or a package override sets call_argument_kinds.",
            "call_argument_kinds",
            false,
            is_set
        ),
        "MOD004" => option_activation!(
            "style or a package override sets str_enum_values.",
            "str_enum_values",
            false,
            is_set
        ),
        "MOD006" => option_activation!(
            "style or a package override sets dataclass_options, including [].",
            "dataclass_options",
            true,
            is_set
        ),
        "IMP004" => option_activation!(
            "style or a package override lists third_party_reexports.",
            "third_party_reexports",
            false,
            truthy
        ),
        "MOD007" => option_activation!(
            "style or a package override sets bases, including {}.",
            "bases",
            true,
            is_set
        ),
        "MOD008" => option_activation!(
            "style or a package override sets errors, including {}.",
            "errors",
            true,
            is_set
        ),
        "VIS008" => option_activation!(
            "style or a package override sets reexports to all or alias.",
            "reexports",
            false,
            |value: &Value| value == "all" || value == "alias"
        ),
        "TST001" => activation!("tests.layout is mirror.", |config| {
            config["tests"]["layout"] == "mirror"
        }),
        "TST003" => activation!(
            "tests.forbidden_mock_paths contains test path patterns.",
            |config| truthy(&config["tests"]["forbidden_mock_paths"])
        ),
        "TST005" => activation!(
            "The tests/excess-patches entry sets max, including zero.",
            max_patches_per_test
        ),
        _ => return None,
    })
}
