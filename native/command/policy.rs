//! Rule activation and severity as `smelt.engine.rule_settings.RuleSettings`
//! resolves them, for the root, workspace members and package contracts.
//!
//! Every configuration the Python settings would reject returns `Err(())`, so
//! the Python CLI reports it instead.
use super::activation::{Activation, Metadata};
use super::finding::Severity;
use super::validate::Inventory;
use crate::checker::patterns::ModulePattern;
use serde::Deserialize;
use serde_json::{Value, json};
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
    #[serde(default)]
    pub activation: Option<Metadata>,
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
    /// Whether the entry states a reason.
    reason: bool,
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

/// The settings that apply to one file or module: its workspace member's and
/// its package contracts', the least specific contract first.
#[derive(Clone, PartialEq, Eq, Hash)]
struct Target {
    member: Option<usize>,
    packages: Vec<usize>,
}

pub struct Policy<'a> {
    catalog: &'a Catalog,
    config: &'a Value,
    raw_layers: &'a [Value],
    layers: Vec<Entries>,
    /// The rules of each package contract, in contract order.
    packages: Vec<(String, Entries)>,
    /// The rules of each workspace member config, in config order.
    members: Vec<(String, Entries)>,
    /// The indexes of `members`, the longest path first.
    member_order: Vec<usize>,
    /// Each member architecture, as the config whose architecture it is.
    scoped: Vec<Value>,
    strict: bool,
}

impl<'a> Policy<'a> {
    /// The settings of `config`, whose rule layers come base first.
    pub fn new(catalog: &'a Catalog, config: &'a Value, layers: &'a [Value]) -> Result<Self, ()> {
        let policy = Self::build(catalog, config, layers, true)?;
        policy.member_override_issues(None)?;
        Ok(policy)
    }

    /// `RuleSettings(..., validate=validate)`.
    fn build(
        catalog: &'a Catalog,
        config: &'a Value,
        layers: &'a [Value],
        validate: bool,
    ) -> Result<Self, ()> {
        let strict = strict_coverage(layers);
        let guard = guard(strict);
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
        let mut members = Vec::new();
        for (path, member) in config["members"].as_object().into_iter().flatten() {
            members.push((
                path.clone(),
                entries(catalog, &member["rules"], &guard, &mut issues),
            ));
        }
        let mut member_order: Vec<usize> = (0..members.len()).collect();
        member_order.sort_by_key(|index| std::cmp::Reverse(members[*index].0.len()));
        let policy = Self {
            catalog,
            config,
            raw_layers: layers,
            layers: resolved_layers,
            packages,
            members,
            member_order,
            scoped: member_policies(config),
            strict,
        };
        issues += policy.root_limit_issues(layers);
        let refinements = config["style"]["overrides"]
            .as_object()
            .into_iter()
            .flatten()
            .map(|(_, override_)| &override_["rules"])
            .chain(
                config["members"]
                    .as_object()
                    .into_iter()
                    .flatten()
                    .map(|(_, member)| &member["rules"]),
            );
        for rules in refinements {
            for (key, value) in rules.as_object().into_iter().flatten() {
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
        if validate {
            issues += policy.root_broad_issues(layers);
        }
        for entry in config["ignore"].as_array().into_iter().flatten() {
            let rule = entry["rule"].as_str().unwrap_or_default();
            if catalog.matching_codes(rule).is_empty() {
                issues += 1;
            }
        }
        if issues > 0 || (validate && policy.policy_issues()) {
            return Err(());
        }
        // Strict coverage stays an error, and only the root rules enable it.
        let member_guards = policy
            .members
            .iter()
            .filter_map(|(_, entries)| entries.get(STRICT_COVERAGE));
        for setting in member_guards {
            if (strict && setting.value != "error") || (!strict && setting.enables()) {
                return Err(());
            }
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

    /// Whether a package contract or workspace member enables the rule locally.
    pub fn has_package_activation(&self, code: &str) -> bool {
        self.packages
            .iter()
            .chain(&self.members)
            .any(|(_, settings)| settings.get(code).is_some_and(Setting::enables))
    }

    /// Whether the root policy or a member architecture declares the rule's policy.
    fn declares(&self, activation: &Activation) -> bool {
        activation.matches(self.config)
            || self.scoped.iter().any(|config| activation.matches(config))
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
            .members
            .iter()
            .chain(&self.packages)
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
            let Some(activation) = Activation::of(rule) else {
                return false;
            };
            if !activation.required {
                return false;
            }
            let enabled = self
                .layers
                .iter()
                .chain(self.members.iter().map(|(_, entries)| entries))
                .chain(self.packages.iter().map(|(_, entries)| entries))
                .any(|layer| layer.get(&rule.code).is_some_and(Setting::enables));
            enabled && !self.declares(&activation)
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

    /// `validate_packages`: `Err` where it would raise.
    pub fn validate_packages(
        &self,
        resolved: &[Resolved],
        inventory: &Inventory,
    ) -> Result<(), ()> {
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
        self.member_override_issues(Some((inventory, resolved)))?;
        // `_validate_scoped_selectors`: a member's or package's broad selector must cap a rule.
        for (path, entries) in &self.members {
            let raw = &self.config["members"][path]["rules"];
            if !self.has_broad_selector(raw) {
                continue;
            }
            let prefix = format!("{path}/");
            let mut targets = HashSet::new();
            for file in inventory
                .python_paths
                .iter()
                .filter(|file| file.starts_with(&prefix))
            {
                let module = module_for(self.config, &inventory.sources, file)?;
                targets.insert(self.target(resolved, module.as_deref(), Some(file)));
            }
            if self.broad_issues(raw, entries, self.active_in(&targets)) > 0 {
                return Err(());
            }
        }
        let contracts = self.config["__structure__"]["contracts"].as_object();
        for (name, contract) in contracts.into_iter().flatten() {
            let raw = &contract["rules"];
            if !self.has_broad_selector(raw) {
                continue;
            }
            let targets: HashSet<Target> = resolved
                .iter()
                .filter(|resolved| resolved.name == *name)
                .map(|contract| {
                    let package = contract.package.as_str();
                    let location = inventory.module_paths.get(package).map(String::as_str);
                    self.target(resolved, Some(package), location)
                })
                .collect();
            let Some(entries) = self.package(name) else {
                continue;
            };
            if self.broad_issues(raw, entries, self.active_in(&targets)) > 0 {
                return Err(());
            }
        }
        Ok(())
    }

    fn has_broad_selector(&self, raw: &Value) -> bool {
        raw.as_object()
            .into_iter()
            .flatten()
            .any(|(key, _)| self.catalog.broad_selection(key).is_some())
    }

    /// The codes of the rules that report in any of `targets`.
    fn active_in(&self, targets: &HashSet<Target>) -> HashSet<&'a str> {
        self.catalog
            .rules
            .iter()
            .filter(|rule| {
                targets
                    .iter()
                    .any(|target| self.severity_at(rule, target, false).is_some())
            })
            .map(|rule| rule.code.as_str())
            .collect()
    }

    /// `_validate_member_overrides`: a member entry without a reason may not lower a
    /// rule below the root's severity, for any of the member's sources when the
    /// inventory is known.
    fn member_override_issues(&self, ctx: Option<(&Inventory, &[Resolved])>) -> Result<(), ()> {
        let Some(base) = self.config.get("__workspace_base__") else {
            return Ok(());
        };
        let root = Policy::build(self.catalog, base, self.raw_layers, false)?;
        let guard = guard(self.strict);
        let base_contracts = base["__structure__"]["contracts"].as_object();
        let root_resolved: Vec<Resolved> = ctx
            .map(|(_, resolved)| resolved)
            .unwrap_or_default()
            .iter()
            .filter(|contract| {
                base_contracts.is_some_and(|names| names.contains_key(&contract.name))
            })
            .map(|contract| Resolved {
                name: contract.name.clone(),
                package: contract.package.clone(),
            })
            .collect();
        for (path, member) in self.config["members"].as_object().into_iter().flatten() {
            let mut maps: Vec<(&Value, Option<&str>)> = vec![(&member["rules"], None)];
            maps.extend(
                member["__structure__"]["contracts"]
                    .as_object()
                    .into_iter()
                    .flatten()
                    .map(|(_, contract)| (&contract["rules"], contract["match"].as_str())),
            );
            let prefix = format!("{path}/");
            for (raw, pattern) in maps {
                let mut ignored = 0;
                let settings = entries(self.catalog, raw, &guard, &mut ignored);
                let pattern = pattern
                    .map(ModulePattern::new)
                    .transpose()
                    .map_err(|_| ())?;
                let mut targets = HashSet::new();
                for (file, module) in ctx
                    .map(|(inventory, _)| &inventory.sources)
                    .into_iter()
                    .flatten()
                {
                    if !file.starts_with(&prefix) {
                        continue;
                    }
                    if let Some(pattern) = &pattern {
                        let segments: Vec<&str> = module.split('.').collect();
                        if !(1..=segments.len())
                            .any(|end| pattern.matches(&segments[..end].join(".")))
                        {
                            continue;
                        }
                    }
                    targets.insert(root.target(&root_resolved, Some(module), Some(file)));
                }
                for (code, setting) in &settings {
                    if guard.contains(code.as_str()) || setting.reason {
                        continue;
                    }
                    let Some(rule) = self.catalog.by_code(code) else {
                        continue;
                    };
                    let option = Activation::of(rule).and_then(|activation| activation.option);
                    let default =
                        if option.is_some_and(|option| base["__structure__"][option].is_null()) {
                            root.apply_layers(code, None, usize::MAX)
                        } else {
                            root.global_value(rule)
                        };
                    let mut highest = rank(default.as_deref().unwrap_or("off"));
                    for target in &targets {
                        if let Some(severity) = root.severity_at(rule, target, false) {
                            highest = highest.max(rank(severity.value()));
                        }
                    }
                    if rank(&setting.value) < highest {
                        return Err(());
                    }
                }
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

    /// `_member_for`: the innermost workspace member with a config that holds `location`.
    fn member_for(&self, location: &str) -> Option<usize> {
        self.member_order.iter().copied().find(|index| {
            let path = self.members[*index].0.as_str();
            location == path
                || location
                    .strip_prefix(path)
                    .is_some_and(|rest| rest.starts_with('/'))
        })
    }

    /// The member and package contracts whose settings apply in `module` at `location`.
    fn target(
        &self,
        resolved: &[Resolved],
        module: Option<&str>,
        location: Option<&str>,
    ) -> Target {
        let member = location.and_then(|location| self.member_for(location));
        let mut packages = Vec::new();
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
            packages.extend(candidates.iter().filter_map(|contract| {
                self.packages
                    .iter()
                    .position(|(name, _)| *name == contract.name)
            }));
        }
        Target { member, packages }
    }

    fn severity_at(&self, rule: &Rule, target: &Target, selected: bool) -> Option<Severity> {
        let mut value = self.default_value(rule);
        for layer in &self.layers {
            if let Some(setting) = layer.get(&rule.code) {
                value = setting.apply(value);
            }
        }
        let member = target.member.map(|index| &self.members[index].1);
        let packages = target.packages.iter().map(|index| &self.packages[*index].1);
        for entries in member.into_iter().chain(packages) {
            if let Some(setting) = entries.get(&rule.code) {
                value = setting.apply(value);
            }
        }
        if value.is_none() && selected {
            value = Some(rule.default_severity.clone());
        }
        value
            .filter(|value| value != "off")
            .and_then(|value| Severity::parse(&value))
    }

    /// `severity_for_path`: the severity of `rule` for a file, `None` when it is
    /// off there; `Err` where the Python engine would raise.
    pub fn severity_for_path(
        &self,
        rule: &Rule,
        resolved: &[Resolved],
        sources: &HashMap<String, String>,
        path: Option<&str>,
        selected: bool,
    ) -> Result<Option<Severity>, ()> {
        let path = path.filter(|path| !path.is_empty());
        let module = match path {
            Some(path) => module_for(self.config, sources, path)?,
            None => None,
        };
        let target = self.target(resolved, module.as_deref(), path);
        Ok(self.severity_at(rule, &target, selected))
    }

    /// `unused_policies`: declared policies whose rule no entry names.
    pub fn unused_policies(&self) -> Vec<(String, String)> {
        let named: HashSet<&str> = self
            .layers
            .iter()
            .chain(self.members.iter().map(|(_, entries)| entries))
            .chain(self.packages.iter().map(|(_, entries)| entries))
            .flat_map(|layer| layer.iter())
            .filter(|(_, setting)| !setting.broad || setting.value == "off")
            .map(|(code, _)| code.as_str())
            .collect();
        let root = self.config.get("__workspace_base__").unwrap_or(self.config);
        let mut issues = Vec::new();
        for rule in &self.catalog.rules {
            let Some(activation) = Activation::of(rule) else {
                continue;
            };
            if rule.enabled_by_default || !self.declares(&activation) {
                continue;
            }
            let root_declares = activation.root_declares(root);
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

/// `_strict_guard`: the codes broad selectors leave alone.
fn guard(strict: bool) -> HashSet<&'static str> {
    if strict {
        HashSet::from([STRICT_COVERAGE])
    } else {
        HashSet::new()
    }
}

/// `architecture_scopes(config)[1:]`: each workspace member's architecture as
/// a config whose architecture it is, by member path.
fn member_policies(config: &Value) -> Vec<Value> {
    let Some(packages) = config
        .get("__workspace_packages__")
        .and_then(Value::as_object)
        .filter(|packages| !packages.is_empty())
    else {
        return Vec::new();
    };
    let mut paths: Vec<&String> = packages.keys().filter(|path| !path.is_empty()).collect();
    paths.sort();
    paths
        .into_iter()
        .map(|path| {
            let mut architecture = config["members"]
                .get(path)
                .and_then(|member| member.get("architecture"))
                .filter(|architecture| !architecture.is_null())
                .cloned()
                .unwrap_or_else(|| json!({}));
            architecture["import_graph"] = config["architecture"]["import_graph"].clone();
            let mut scoped = config.clone();
            scoped["architecture"] = architecture;
            scoped
        })
        .collect()
}

/// `severity_for_path`'s module: the source at `path`, or the module a path
/// below a source root would be; `Err` where the Python engine would raise.
pub fn module_for(
    config: &Value,
    sources: &HashMap<String, String>,
    path: &str,
) -> Result<Option<String>, ()> {
    if let Some(module) = sources.get(path) {
        return Ok(Some(module.clone()));
    }
    let project = &config["project"];
    let roots: Vec<&str> = project["root_packages"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    for source_root in project["source_roots"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        let relative = if source_root == "." {
            Some(path)
        } else if path == source_root {
            Some("")
        } else {
            path.strip_prefix(source_root)
                .and_then(|rest| rest.strip_prefix('/'))
        };
        let Some(relative) = relative else {
            continue;
        };
        if relative.is_empty() || relative == "." {
            // `Path(".").with_suffix("")` raises in the Python engine.
            return Err(());
        }
        let mut parts: Vec<&str> = relative
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .collect();
        if let Some(last) = parts.last_mut() {
            *last = without_suffix(last);
        }
        if parts.first().is_some_and(|first| roots.contains(first)) {
            if parts.last() == Some(&"__init__") {
                parts.pop();
            }
            return Ok(Some(parts.join(".")));
        }
        return Ok(None);
    }
    Ok(None)
}

/// `PurePath.with_suffix("")` for one name.
fn without_suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(index) if index > 0 && index < name.len() - 1 => &name[..index],
        _ => name,
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
    for (_, key, codes, value) in broad {
        let reason = has_reason(&raw[key.as_str()]);
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
                        reason,
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
                        reason: has_reason(&raw[key.as_str()]),
                    },
                );
            }
        }
    }
    result
}

/// Whether a rule entry states a reason.
fn has_reason(setting: &Value) -> bool {
    setting
        .get("reason")
        .is_some_and(|reason| !reason.is_null())
}
