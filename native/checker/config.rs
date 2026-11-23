//! The resolved policy the core receives until the YAML loader is ported.
use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::PathBuf;

pub type RuleMap = BTreeMap<String, RuleSetting>;

#[derive(Deserialize)]
pub struct Request {
    pub root: PathBuf,
    pub select: Vec<String>,
    pub strict_coverage: bool,
    pub project: Project,
    pub contracts: BTreeMap<String, Contract>,
    pub project_policy: Policy,
    pub defaults: Policy,
    pub rule_layers: Vec<RuleMap>,
    pub members: BTreeMap<String, serde_json::Value>,
    pub ignore_entries: Vec<IgnoreEntry>,
}

#[derive(Deserialize)]
pub struct Project {
    pub root_packages: Vec<String>,
    pub source_roots: Vec<String>,
    pub exclude: Vec<String>,
}

#[derive(Deserialize)]
pub struct Contract {
    #[serde(rename = "match")]
    pub pattern: String,
    pub style_override: bool,
    pub rules: RuleMap,
    #[serde(flatten)]
    pub policy: Policy,
}

#[derive(Clone, Copy, Deserialize)]
pub struct Policy {
    pub max_positional_parameters: Option<usize>,
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

#[derive(Deserialize)]
pub struct IgnoreEntry {
    pub rule: String,
    pub modules: Vec<String>,
    pub paths: Vec<String>,
}
