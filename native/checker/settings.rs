//! Rule activation and severity with the precedence of `RuleSettings`.
use super::config::{Request, RuleMap};
use super::contracts::Contracts;
use super::patterns::is_within;
use super::rules::PORTED;
use serde::Deserialize;
use std::collections::{BTreeSet, HashMap, HashSet};

const ALL_RULES: &str = "ALL";
const STRICT_COVERAGE: &str = "ARC013";

#[derive(Deserialize)]
pub struct CatalogRule {
    pub code: String,
    pub name: String,
    pub category: String,
    pub default_severity: String,
    pub enabled_by_default: bool,
    pub scope: String,
}

struct Setting<'a> {
    value: &'a str,
    broad: bool,
}

impl<'a> Setting<'a> {
    fn enables(&self) -> bool {
        !self.broad && self.value != "off"
    }

    fn apply(&self, inherited: Option<&'a str>) -> Option<&'a str> {
        if !self.broad || self.value == "off" {
            return Some(self.value);
        }
        let current = inherited?;
        Some(if rank(self.value) < rank(current) {
            self.value
        } else {
            current
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

type Entries<'a> = HashMap<&'a str, Setting<'a>>;

pub struct Settings<'a> {
    catalog: &'a [CatalogRule],
    layers: Vec<Entries<'a>>,
    packages: HashMap<&'a str, Entries<'a>>,
}

impl<'a> Settings<'a> {
    pub fn new(catalog: &'a [CatalogRule], request: &'a Request) -> Self {
        let mut guard = HashSet::new();
        if request.strict_coverage {
            guard.insert(STRICT_COVERAGE);
        }
        let project_rules: HashSet<&str> = catalog
            .iter()
            .filter(|rule| rule.scope != "source")
            .map(|rule| rule.code.as_str())
            .collect();
        let layers = request
            .rule_layers
            .iter()
            .map(|layer| entries(catalog, layer, &guard))
            .collect();
        let packages = request
            .contracts
            .iter()
            .map(|(name, contract)| {
                (
                    name.as_str(),
                    entries(catalog, &contract.rules, &project_rules),
                )
            })
            .collect();
        Self {
            catalog,
            layers,
            packages,
        }
    }

    /// The ported rules this selection runs.
    pub fn active(&self, select: &[String]) -> Vec<&'a CatalogRule> {
        self.catalog
            .iter()
            .filter(|rule| PORTED.contains(&rule.code.as_str()))
            .filter(|rule| self.is_active(rule, select))
            .collect()
    }

    fn is_active(&self, rule: &'a CatalogRule, select: &[String]) -> bool {
        let selected = select.iter().any(|prefix| {
            let wanted = prefix.trim().to_uppercase();
            wanted == ALL_RULES || rule.code.starts_with(&wanted)
        });
        let setting = self.global_value(rule);
        let package_enabled = self.packages.values().any(|settings| {
            settings
                .get(rule.code.as_str())
                .is_some_and(Setting::enables)
        });
        if setting == Some("off") && !package_enabled {
            return false;
        }
        let enabled = setting.is_some_and(|value| value != "off") || selected || package_enabled;
        enabled && (select.is_empty() || selected)
    }

    /// The severity an active rule runs with: its setting, else its default.
    pub fn active_severity(&self, rule: &'a CatalogRule) -> &'a str {
        self.global_value(rule)
            .filter(|value| *value != "off")
            .unwrap_or(rule.default_severity.as_str())
    }

    fn global_value(&self, rule: &'a CatalogRule) -> Option<&'a str> {
        let mut value = rule
            .enabled_by_default
            .then_some(rule.default_severity.as_str());
        for layer in &self.layers {
            if let Some(setting) = layer.get(rule.code.as_str()) {
                value = setting.apply(value);
            }
        }
        value
    }

    /// The severity of a finding in `module`; `None` when the rule is off there.
    pub fn severity(
        &self,
        rule: &'a CatalogRule,
        contracts: &Contracts<'_>,
        module: &str,
        selected: bool,
    ) -> Option<&'a str> {
        let mut value = rule
            .enabled_by_default
            .then_some(rule.default_severity.as_str());
        for layer in &self.layers {
            if let Some(setting) = layer.get(rule.code.as_str()) {
                value = setting.apply(value);
            }
        }
        let mut candidates: Vec<_> = contracts
            .resolved
            .iter()
            .filter(|contract| is_within(module, contract.package))
            .collect();
        candidates.sort_by_key(|contract| contract.package.len());
        for contract in candidates {
            if let Some(setting) = self
                .packages
                .get(contract.name)
                .and_then(|entries| entries.get(rule.code.as_str()))
            {
                value = setting.apply(value);
            }
        }
        if value.is_none() && selected {
            value = Some(rule.default_severity.as_str());
        }
        value.filter(|severity| *severity != "off")
    }

    /// Resolve a name, family, code prefix or ALL against the catalog.
    pub fn matching_codes(&self, selector: &str) -> BTreeSet<String> {
        let wanted = selector.trim();
        if let Some(codes) = family_codes(self.catalog, wanted) {
            return codes.into_iter().map(str::to_owned).collect();
        }
        let lowered = wanted.to_lowercase();
        if let Some(rule) = self
            .catalog
            .iter()
            .find(|rule| lowered.to_uppercase() == rule.code || lowered == rule.name)
        {
            return BTreeSet::from([rule.code.clone()]);
        }
        let prefix = wanted.to_uppercase();
        self.catalog
            .iter()
            .filter(|rule| {
                !prefix.is_empty() && (prefix == ALL_RULES || rule.code.starts_with(&prefix))
            })
            .map(|rule| rule.code.clone())
            .collect()
    }
}

/// Resolve selectors to settings: ALL, then families, then individual rules.
fn entries<'a>(
    catalog: &'a [CatalogRule],
    raw: &'a RuleMap,
    excluded: &HashSet<&str>,
) -> Entries<'a> {
    let mut result = Entries::new();
    let mut broad: Vec<(u8, &str, Vec<&'a str>, &'a str)> = raw
        .iter()
        .filter_map(|(key, setting)| {
            let value = setting.value()?;
            let (level, codes) = broad_selection(catalog, key)?;
            Some((level, key.as_str(), codes, value))
        })
        .collect();
    broad.sort_by_key(|item| (item.0, item.1));
    for (_, _, codes, value) in broad {
        for code in codes {
            if !excluded.contains(code) {
                result.insert(code, Setting { value, broad: true });
            }
        }
    }
    for (key, setting) in raw {
        let Some(value) = setting.value() else {
            continue;
        };
        if broad_selection(catalog, key).is_some() {
            continue;
        }
        if let Some(rule) = catalog
            .iter()
            .find(|rule| rule.code == *key || rule.name == *key)
        {
            let value = if value == "on" {
                rule.default_severity.as_str()
            } else {
                value
            };
            result.insert(
                rule.code.as_str(),
                Setting {
                    value,
                    broad: false,
                },
            );
        }
    }
    result
}

fn broad_selection<'a>(catalog: &'a [CatalogRule], key: &str) -> Option<(u8, Vec<&'a str>)> {
    if key == ALL_RULES {
        return Some((0, catalog.iter().map(|rule| rule.code.as_str()).collect()));
    }
    family_codes(catalog, key).map(|codes| (1, codes))
}

fn family_codes<'a>(catalog: &'a [CatalogRule], selector: &str) -> Option<Vec<&'a str>> {
    let lowered = selector.trim().to_lowercase();
    let wanted = lowered.strip_suffix('/').unwrap_or(&lowered);
    let codes: Vec<&str> = catalog
        .iter()
        .filter(|rule| rule.category == wanted)
        .map(|rule| rule.code.as_str())
        .collect();
    (!codes.is_empty()).then_some(codes)
}
