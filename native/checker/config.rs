//! The configuration loader and the resolved policy the core runs with.
mod compose;
mod discovery;
pub mod errors;
pub mod load;
pub mod models;
mod paths;
pub mod schema;
mod semantics;
mod workspace;
pub mod yaml;

use schema::string_list;
use serde::Deserialize;
use serde_json::{Map, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub type RuleMap = BTreeMap<String, RuleSetting>;

pub struct Request {
    pub root: PathBuf,
    pub select: Vec<String>,
    pub strict_coverage: bool,
    pub project: Project,
    pub contracts: BTreeMap<String, Contract>,
    pub project_policy: Policy,
    pub rule_layers: Vec<RuleMap>,
    pub members: BTreeMap<String, Value>,
    pub ignore_entries: Vec<IgnoreEntry>,
}

impl Request {
    /// Load `config` and resolve the policy for the selected rules.
    pub fn load(config: &Path, select: Vec<String>) -> Result<Self, String> {
        let loaded = load::load_config(config).map_err(|error| error.render())?;
        let policy = &loaded.config;
        let structure = &policy["__structure__"];
        let root = loaded.path.parent().unwrap_or(&loaded.path).to_path_buf();
        let exclude = policy["project"]["exclude"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|entry| entry.as_str().or_else(|| entry["path"].as_str()))
            .map(str::to_owned)
            .collect();
        let mut contracts = BTreeMap::new();
        for (name, contract) in structure["contracts"].as_object().into_iter().flatten() {
            contracts.insert(
                name.clone(),
                Contract {
                    pattern: contract["match"].as_str().unwrap_or_default().to_owned(),
                    style_override: contract["__style_override__"] == Value::Bool(true),
                    rules: rule_map(&contract["rules"])?,
                    policy: Policy::from_value(contract),
                },
            );
        }
        let rule_layers = load::rule_layers(policy)
            .into_iter()
            .map(rule_map)
            .collect::<Result<_, _>>()?;
        let ignore_entries = policy["ignore"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|entry| IgnoreEntry {
                rule: entry["rule"].as_str().unwrap_or_default().to_owned(),
                modules: string_list(entry.get("modules")),
                paths: string_list(entry.get("paths")),
            })
            .collect();
        Ok(Self {
            root,
            select,
            strict_coverage: load::strict_coverage(policy),
            project: Project {
                root_packages: string_list(policy["project"].get("root_packages")),
                source_roots: string_list(policy["project"].get("source_roots")),
                exclude,
                python_version: policy["project"]["python_version"]
                    .as_str()
                    .map(str::to_owned),
            },
            contracts,
            project_policy: Policy::from_value(structure),
            rule_layers,
            members: policy["members"]
                .as_object()
                .map(|members| {
                    members
                        .iter()
                        .map(|(path, member)| (path.clone(), member.clone()))
                        .collect()
                })
                .unwrap_or_default(),
            ignore_entries,
        })
    }
}

fn rule_map(rules: &Value) -> Result<RuleMap, String> {
    serde_json::from_value(rules.clone()).map_err(|error| error.to_string())
}

pub struct Project {
    pub root_packages: Vec<String>,
    pub source_roots: Vec<String>,
    pub exclude: Vec<String>,
    pub python_version: Option<String>,
}

pub struct Contract {
    pub pattern: String,
    pub style_override: bool,
    pub rules: RuleMap,
    pub policy: Policy,
}

/// The policy options one scope sets; options left unset are absent.
pub struct Policy {
    values: Map<String, Value>,
}

impl Policy {
    fn from_value(policy: &Value) -> Self {
        let values = models::POLICY_FIELDS
            .iter()
            .chain(&models::RULE_LIMITS)
            .filter_map(|name| {
                let value = policy.get(*name).filter(|value| !value.is_null())?;
                Some(((*name).to_owned(), value.clone()))
            })
            .collect();
        Self { values }
    }

    pub fn option(&self, name: &str) -> Option<&Value> {
        self.values.get(name)
    }
}

#[derive(Deserialize)]
#[serde(untagged)]
pub enum RuleSetting {
    Level(String),
    Override { severity: Option<String> },
}

impl RuleSetting {
    /// The severity an entry sets; `None` when it only sets the rule's limit.
    pub fn value(&self) -> Option<&str> {
        match self {
            Self::Level(value) => Some(value),
            Self::Override { severity } => severity.as_deref(),
        }
    }
}

pub struct IgnoreEntry {
    pub rule: String,
    pub modules: Vec<String>,
    pub paths: Vec<String>,
}
