//! The configuration loader and the resolved policy the core runs with.
pub mod architecture;
mod compose;
mod discovery;
pub mod errors;
pub mod load;
pub mod models;
pub(crate) mod paths;
pub mod schema;
mod semantics;
pub(crate) mod workspace;
pub mod yaml;

use architecture::Architecture;
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
    pub architecture: Architecture,
    pub tests: TestPolicy,
    pub contracts: BTreeMap<String, Contract>,
    /// `architecture.canonical_exports`: a definition and its supported public export.
    pub canonical_exports: BTreeMap<String, String>,
    pub project_policy: Policy,
    pub forbidden_names: Vec<String>,
    pub file_naming: FileNaming,
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
                    required: string_list(contract.get("required")),
                    public: contract
                        .get("public")
                        .filter(|value| !value.is_null())
                        .map(|value| string_list(Some(value))),
                    min_imports_to_bundle: limit(&contract["min_imports_to_bundle"]),
                    min_importers_to_export: limit(&contract["min_importers_to_export"]),
                    count_tests: contract["count_tests"].as_bool().unwrap_or(false),
                    mirrored_test_access: contract["test_access"] == "mirrored",
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
                test_roots: string_list(policy["project"].get("test_roots")),
                exclude,
                python_version: policy["project"]["python_version"]
                    .as_str()
                    .map(str::to_owned),
                uv_workspace: policy["project"]["workspace"] == "uv",
            },
            architecture: Architecture::from_config(&policy["architecture"])?,
            tests: TestPolicy::from_config(policy),
            contracts,
            canonical_exports: structure["canonical_exports"]
                .as_object()
                .into_iter()
                .flatten()
                .filter_map(|(symbol, export)| Some((symbol.clone(), export.as_str()?.to_owned())))
                .collect(),
            project_policy: Policy::from_value(structure),
            forbidden_names: string_list(structure.get("forbidden_names")),
            file_naming: FileNaming {
                yaml_extension: structure["file_naming"]["yaml_extension"]
                    .as_str()
                    .map(str::to_owned),
                compose_basename: structure["file_naming"]["compose_basename"]
                    .as_str()
                    .map(str::to_owned),
            },
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

fn limit(value: &Value) -> Option<usize> {
    value.as_u64().and_then(|value| usize::try_from(value).ok())
}

fn rule_map(rules: &Value) -> Result<RuleMap, String> {
    serde_json::from_value(rules.clone()).map_err(|error| error.to_string())
}

pub struct Project {
    pub root_packages: Vec<String>,
    pub source_roots: Vec<String>,
    pub test_roots: Vec<String>,
    pub exclude: Vec<String>,
    pub python_version: Option<String>,
    /// `project.workspace: uv`: the root analyzes its declared uv workspace members.
    pub uv_workspace: bool,
}

/// The canonical YAML spellings of `style.file_naming`.
pub struct FileNaming {
    pub yaml_extension: Option<String>,
    pub compose_basename: Option<String>,
}

/// The `tests` section and the project-wide patch limit.
pub struct TestPolicy {
    /// `mirror` or `none`.
    pub layout: String,
    pub mirror: String,
    pub mirror_exclude: Vec<String>,
    pub mirror_suffixes: bool,
    pub quality_exclude: Vec<String>,
    pub forbidden_mock_paths: Vec<String>,
    pub assertion_helpers: Vec<String>,
    pub timeout_helpers: Vec<String>,
    pub max_patches_per_test: Option<u64>,
}

impl TestPolicy {
    fn from_config(policy: &Value) -> Self {
        let tests = &policy["tests"];
        let rules = &policy["rules"];
        Self {
            layout: tests["layout"].as_str().unwrap_or("none").to_owned(),
            mirror: tests["mirror"]
                .as_str()
                .unwrap_or("{path}/test_{module}.py")
                .to_owned(),
            mirror_exclude: string_list(tests.get("mirror_exclude")),
            mirror_suffixes: tests["mirror_suffixes"].as_bool().unwrap_or(false),
            quality_exclude: string_list(tests.get("quality_exclude")),
            forbidden_mock_paths: string_list(tests.get("forbidden_mock_paths")),
            assertion_helpers: string_list(tests.get("assertion_helpers")),
            timeout_helpers: string_list(tests.get("timeout_helpers")),
            // Both spellings may be set; the name is read last, so it wins.
            max_patches_per_test: ["TST005", "tests/excess-patches"]
                .iter()
                .rev()
                .find_map(|key| rules[*key]["max"].as_u64()),
        }
    }
}

pub struct Contract {
    pub pattern: String,
    pub style_override: bool,
    pub required: Vec<String>,
    /// Public entry points relative to the package; `None` leaves it unrestricted.
    pub public: Option<Vec<String>>,
    pub min_imports_to_bundle: Option<usize>,
    pub min_importers_to_export: Option<usize>,
    pub count_tests: bool,
    /// `test_access: mirrored`: tests mirroring a module count as inside the package.
    pub mirrored_test_access: bool,
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
