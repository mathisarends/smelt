//! The `architecture` section the dependency rules read.
use super::schema::string_list;
use crate::checker::patterns::{ModulePattern, is_within};
use serde_json::Value;

const DEFAULT_CYCLE_SCOPES: [&str; 3] = ["features", "layers", "siblings"];

pub struct Architecture {
    pub features: Option<Features>,
    pub shared: Vec<String>,
    pub composition_root: Vec<String>,
    pub wiring: Vec<ModulePattern>,
    pub layers: Vec<Layer>,
    pub cross_feature: CrossFeature,
    pub import_graph: ImportGraphPolicy,
    /// `architecture.members`: a member path glob and the member globs it may import.
    pub members: Vec<(String, Vec<String>)>,
}

impl Architecture {
    pub fn from_config(section: &Value) -> Result<Self, String> {
        let wiring = string_list(section.get("wiring"))
            .iter()
            .map(|pattern| ModulePattern::new(pattern))
            .collect::<Result<_, _>>()?;
        let layers = section["layers"]
            .as_object()
            .into_iter()
            .flatten()
            .map(|(name, layer)| Layer::from_config(name, layer))
            .collect();
        Ok(Self {
            features: Features::from_config(&section["features"]),
            shared: string_list(section.get("shared")),
            composition_root: string_list(section.get("composition_root")),
            wiring,
            layers,
            cross_feature: CrossFeature::from_config(&section["cross_feature"]),
            import_graph: ImportGraphPolicy::from_config(&section["import_graph"]),
            members: section["members"]
                .as_object()
                .into_iter()
                .flatten()
                .map(|(pattern, member)| {
                    (pattern.clone(), string_list(member.get("may_depend_on")))
                })
                .collect(),
        })
    }

    pub fn layer(&self, name: &str) -> Option<&Layer> {
        self.layers.iter().find(|layer| layer.name == name)
    }

    /// The explicit module prefixes of each layer, in declaration order.
    pub fn module_bindings(&self) -> impl Iterator<Item = (&str, &str)> {
        self.layers.iter().flat_map(|layer| {
            layer
                .modules
                .iter()
                .map(|prefix| (prefix.as_str(), layer.name.as_str()))
        })
    }
}

pub struct Features {
    pub root: Option<String>,
    pub pattern: Option<String>,
}

impl Features {
    fn from_config(section: &Value) -> Option<Self> {
        section.is_object().then(|| Self {
            root: section["root"].as_str().map(str::to_owned),
            pattern: section["pattern"].as_str().map(str::to_owned),
        })
    }
}

pub struct Layer {
    pub name: String,
    pub path: Option<String>,
    pub modules: Vec<String>,
    pub may_depend_on: Vec<String>,
    pub third_party: ThirdParty,
}

impl Layer {
    fn from_config(name: &str, section: &Value) -> Self {
        Self {
            name: name.to_owned(),
            path: section["path"].as_str().map(str::to_owned),
            modules: string_list(section.get("modules")),
            may_depend_on: string_list(section.get("may_depend_on")),
            third_party: ThirdParty::from_config(&section["third_party"]),
        }
    }
}

pub struct ThirdParty {
    pub deny_by_default: bool,
    pub allow: Vec<String>,
    pub deny: Vec<String>,
}

impl ThirdParty {
    fn from_config(section: &Value) -> Self {
        let default = section.as_str().or_else(|| section["default"].as_str());
        Self {
            deny_by_default: default == Some("deny"),
            allow: string_list(section.get("allow")),
            deny: string_list(section.get("deny")),
        }
    }

    /// Whether this policy rejects any of the imported `names`.
    pub fn denies(&self, names: &[&str]) -> bool {
        let listed =
            |name: &str, entries: &[String]| entries.iter().any(|entry| is_within(name, entry));
        if self.deny_by_default {
            !names.iter().all(|name| listed(name, &self.allow))
        } else {
            names.iter().any(|name| listed(name, &self.deny))
        }
    }
}

enum Allowance {
    /// `layer -> layer` for every pair of features.
    Layers {
        label: String,
        source: String,
        target: String,
    },
    /// `{from: feature.layer, to: feature.layer}`.
    Edge { source: String, target: String },
}

pub struct CrossFeature {
    pub deny_by_default: bool,
    allow: Vec<Allowance>,
}

impl CrossFeature {
    fn from_config(section: &Value) -> Self {
        let allow = section["allow"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Allowance::from_config)
            .collect();
        Self {
            deny_by_default: section["default"].as_str() != Some("allow"),
            allow,
        }
    }

    pub fn allows(
        &self,
        (source_feature, source_layer): (&str, &str),
        (target_feature, target_layer): (&str, &str),
    ) -> bool {
        let source_edge = format!("{source_feature}.{source_layer}");
        let target_edge = format!("{target_feature}.{target_layer}");
        self.allow.iter().any(|allowance| match allowance {
            Allowance::Layers { source, target, .. } => {
                source == source_layer && target == target_layer
            }
            Allowance::Edge { source, target } => *source == source_edge && *target == target_edge,
        })
    }

    pub fn labels(&self) -> Vec<String> {
        self.allow
            .iter()
            .map(|allowance| match allowance {
                Allowance::Layers { label, .. } => label.clone(),
                Allowance::Edge { source, target } => format!("{source} -> {target}"),
            })
            .collect()
    }
}

impl Allowance {
    fn from_config(entry: &Value) -> Option<Self> {
        if let Some(label) = entry.as_str() {
            let (source, target) = label.split_once("->")?;
            return Some(Self::Layers {
                label: label.to_owned(),
                source: trim_python(source).to_owned(),
                target: trim_python(target).to_owned(),
            });
        }
        let side = |alias: &str, field: &str| entry[alias].as_str().or(entry[field].as_str());
        Some(Self::Edge {
            source: side("from", "source")?.to_owned(),
            target: side("to", "target")?.to_owned(),
        })
    }
}

/// Trim what Python's `\s` matches, which includes U+001C to U+001F.
fn trim_python(text: &str) -> &str {
    text.trim_matches(|character: char| {
        character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
    })
}

pub struct ImportGraphPolicy {
    pub include_type_checking: bool,
    pub transitive: bool,
    pub cycles: Vec<String>,
    pub ignore_type_checking_cycles: bool,
}

impl ImportGraphPolicy {
    fn from_config(section: &Value) -> Self {
        let cycles = if section.get("cycles").is_some_and(Value::is_array) {
            string_list(section.get("cycles"))
        } else {
            DEFAULT_CYCLE_SCOPES.map(str::to_owned).to_vec()
        };
        Self {
            include_type_checking: section["type_checking"].as_str() != Some("ignore"),
            transitive: section["transitive"].as_bool().unwrap_or(false),
            cycles,
            ignore_type_checking_cycles: section["type_checking_cycles"].as_str() == Some("ignore"),
        }
    }
}
