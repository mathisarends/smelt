//! The configuration loader and the resolved policy the core runs with.
pub mod architecture;
pub(crate) mod compose;
pub(crate) mod discovery;
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
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub type RuleMap = BTreeMap<String, RuleSetting>;

pub struct Request {
    pub root: PathBuf,
    /// The loaded config file.
    pub config_path: PathBuf,
    /// The file that declared each explicitly set key, for composed configs.
    pub origins: BTreeMap<String, String>,
    pub select: Vec<String>,
    pub strict_coverage: bool,
    /// `suppressions.require_reason`: an inline suppression needs a `-- reason`.
    pub require_reason: bool,
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
    /// The file of each rule layer, parallel to `rule_layers`.
    pub rule_sources: Vec<String>,
    /// The max of the root `meta/uncovered-package-facade` entry, else 2.
    pub max_uncovered_facade_callers: usize,
    pub ignore_entries: Vec<IgnoreEntry>,
    /// The top-level packages of the workspace root (`""`) and of each member.
    pub workspace_packages: BTreeMap<String, Vec<String>>,
    /// The architecture of each workspace member: its own, with the root's import
    /// graph; a member without one has the root's import graph only.
    pub member_architectures: BTreeMap<String, Architecture>,
    /// The rules of each member's own config, by member path.
    pub member_rules: Vec<(String, RuleMap)>,
}

impl Request {
    /// Load `config` and resolve the policy for the selected rules.
    pub fn load(config: &Path, select: Vec<String>) -> Result<Self, String> {
        let loaded = load::load_config(config).map_err(|error| error.render())?;
        Self::from_loaded(&loaded, select)
    }

    /// Resolve the policy of a loaded configuration for the selected rules.
    pub fn from_loaded(loaded: &load::LoadedConfig, select: Vec<String>) -> Result<Self, String> {
        let policy = &loaded.config;
        let structure = &policy["__structure__"];
        let root = loaded.path.parent().unwrap_or(&loaded.path).to_path_buf();
        let exclusions: Vec<Exclusion> = policy["project"]["exclude"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|entry| {
                let path = entry.as_str().or_else(|| entry["path"].as_str())?;
                Some(Exclusion {
                    path: path.to_owned(),
                    reason: entry["reason"].as_str().map(str::to_owned),
                })
            })
            .collect();
        let exclude = exclusions.iter().map(|entry| entry.path.clone()).collect();
        let mut contracts = BTreeMap::new();
        for (name, contract) in structure["contracts"].as_object().into_iter().flatten() {
            contracts.insert(
                name.clone(),
                Contract {
                    pattern: contract["match"].as_str().unwrap_or_default().to_owned(),
                    style_override: contract["__style_override__"] == Value::Bool(true),
                    style_location: contract["__style_location__"].as_str().map(str::to_owned),
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
        // Both spellings may be set; the name is read last, so it wins.
        let max_uncovered_facade_callers = ["MET005", "meta/uncovered-package-facade"]
            .iter()
            .rev()
            .find_map(|key| policy["rules"][*key]["max"].as_u64())
            .and_then(|limit| usize::try_from(limit).ok())
            .unwrap_or(2);
        Ok(Self {
            root,
            config_path: loaded.path.clone(),
            origins: loaded.origins.clone(),
            rule_sources: loaded.rule_sources.clone(),
            max_uncovered_facade_callers,
            select,
            strict_coverage: load::strict_coverage(policy),
            require_reason: policy["suppressions"]["require_reason"]
                .as_bool()
                .unwrap_or(true),
            project: Project {
                root_packages: string_list(policy["project"].get("root_packages")),
                source_roots: string_list(policy["project"].get("source_roots")),
                test_roots: string_list(policy["project"].get("test_roots")),
                exclude,
                exclusions,
                python_version: policy["project"]["python_version"]
                    .as_str()
                    .map(str::to_owned),
                uv_workspace: policy["project"]["workspace"] == "uv",
            },
            architecture: Architecture::from_config(&policy["architecture"])?,
            member_architectures: member_architectures(policy)?,
            member_rules: policy["members"]
                .as_object()
                .into_iter()
                .flatten()
                .map(|(path, member)| Ok((path.clone(), rule_map(&member["rules"])?)))
                .collect::<Result<_, String>>()?,
            workspace_packages: policy["__workspace_packages__"]
                .as_object()
                .into_iter()
                .flatten()
                .map(|(path, packages)| (path.clone(), string_list(Some(packages))))
                .collect(),
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
            ignore_entries,
        })
    }
}

fn limit(value: &Value) -> Option<usize> {
    value.as_u64().and_then(|value| usize::try_from(value).ok())
}

/// Local architecture belongs to the member; only the import graph is the root's.
fn member_architectures(policy: &Value) -> Result<BTreeMap<String, Architecture>, String> {
    let import_graph = &policy["architecture"]["import_graph"];
    policy["__workspace_packages__"]
        .as_object()
        .into_iter()
        .flatten()
        .map(|(path, _)| path)
        .filter(|path| !path.is_empty())
        .map(|path| {
            let mut local = match &policy["members"][path.as_str()]["architecture"] {
                Value::Object(architecture) => architecture.clone(),
                _ => Map::new(),
            };
            local.insert("import_graph".to_owned(), import_graph.clone());
            Ok((
                path.clone(),
                Architecture::from_config(&Value::Object(local))?,
            ))
        })
        .collect()
}

impl Request {
    /// Enable architecture/uncovered-source for one run, as `--strict-coverage` does.
    pub fn force_strict_coverage(&mut self) -> Result<(), String> {
        if !self.strict_coverage {
            self.rule_layers.push(rule_map(
                &json!({"architecture/uncovered-source": "error"}),
            )?);
            self.strict_coverage = true;
        }
        Ok(())
    }
}

fn rule_map(rules: &Value) -> Result<RuleMap, String> {
    if rules.is_null() {
        return Ok(RuleMap::new());
    }
    serde_json::from_value(rules.clone()).map_err(|error| error.to_string())
}

pub struct Project {
    pub root_packages: Vec<String>,
    pub source_roots: Vec<String>,
    pub test_roots: Vec<String>,
    pub exclude: Vec<String>,
    /// The `project.exclude` entries with the reason a `{path, reason}` entry gives.
    pub exclusions: Vec<Exclusion>,
    pub python_version: Option<String>,
    /// `project.workspace: uv`: the root analyzes its declared uv workspace members.
    pub uv_workspace: bool,
}

/// One `project.exclude` entry.
pub struct Exclusion {
    pub path: String,
    pub reason: Option<String>,
}

/// The canonical YAML spellings of `style.file_naming`.
pub struct FileNaming {
    pub yaml_extension: Option<String>,
    pub compose_basename: Option<String>,
}

impl FileNaming {
    /// Whether the policy names YAML files, which makes them part of a check.
    pub fn checks_yaml(&self) -> bool {
        self.yaml_extension.is_some() || self.compose_basename.is_some()
    }
}

/// An issue reference or a link, as `tests.skip_reason_pattern` defaults to.
pub const DEFAULT_SKIP_REASON_PATTERN: &str = r"#\d+|https?://\S+";

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
    /// What a quarantine reason must match, such as an issue reference (TST013).
    pub skip_reason_pattern: String,
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
            skip_reason_pattern: tests["skip_reason_pattern"]
                .as_str()
                .unwrap_or(DEFAULT_SKIP_REASON_PATTERN)
                .to_owned(),
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
    /// Where the style part of the contract is configured, if not `style.overrides.<name>`.
    pub style_location: Option<String>,
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
