//! The policy conditions of `smelt.rules.activation.POLICIES`.
//!
//! The generated catalog carries each activation's description, option and
//! whether its policy is required; only the conditions, which are code, live
//! here. A test keeps the two in step.
use super::policy::Rule;
use super::pyjson::truthy;
use serde::Deserialize;
use serde_json::Value;

/// `PolicyActivation`'s data, as the catalog carries it.
#[derive(Deserialize)]
pub struct Metadata {
    pub description: String,
    pub option: Option<String>,
    pub required: bool,
}

/// What declares a rule's policy.
#[derive(Clone, Copy)]
enum Condition {
    /// A condition on the whole configuration.
    Config(fn(&Value) -> bool),
    /// Which values of the activation's option, under style or in any
    /// contract, declare the policy.
    Option(fn(&Value) -> bool),
}

/// `PolicyActivation`: the catalog's data with its condition.
pub struct Activation<'a> {
    pub description: &'a str,
    pub option: Option<&'a str>,
    pub required: bool,
    condition: Condition,
}

impl<'a> Activation<'a> {
    /// The rule's activation; `None` for a rule no policy activates.
    pub fn of(rule: &'a Rule) -> Option<Self> {
        let metadata = rule.activation.as_ref()?;
        Some(Self {
            description: &metadata.description,
            option: metadata.option.as_deref(),
            required: metadata.required,
            condition: condition(&rule.code)?,
        })
    }

    /// Whether a configuration declares the policy.
    pub fn matches(&self, config: &Value) -> bool {
        match (self.condition, self.option) {
            (Condition::Config(matches), _) => matches(config),
            (Condition::Option(accept), Some(option)) => {
                policies(config).any(|policy| accept(&policy[option]))
            }
            (Condition::Option(_), None) => false,
        }
    }

    /// Whether the root declares the policy: an option only under its style.
    pub fn root_declares(&self, root: &Value) -> bool {
        match (self.condition, self.option) {
            (Condition::Option(accept), Some(option)) => accept(&root["__structure__"][option]),
            _ => self.matches(root),
        }
    }
}

fn is_set(value: &Value) -> bool {
    !value.is_null()
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

fn max_patches_per_test(config: &Value) -> bool {
    let rules = &config["rules"];
    ["TST005", "tests/excess-patches"].iter().any(|key| {
        rules[*key].is_object() && rules[*key].get("max").is_some_and(|max| !max.is_null())
    })
}

fn condition(code: &str) -> Option<Condition> {
    Some(match code {
        "LAY007" => Condition::Config(|config| {
            let naming = &config["__structure__"]["file_naming"];
            is_set(&naming["yaml_extension"]) || is_set(&naming["compose_basename"])
        }),
        "LAY013" => Condition::Config(|config| {
            config["project"]["workspace"] == "uv" && is_set(&config["project"]["workspace_layout"])
        }),
        "ARC001" => Condition::Config(|config| truthy(&config["architecture"]["layers"])),
        "ARC002" => Condition::Config(|config| is_set(&config["architecture"]["features"])),
        "ARC003" => Condition::Config(|config| {
            config["architecture"]["layers"]
                .as_object()
                .into_iter()
                .flatten()
                .any(|(_, layer)| {
                    layer["third_party"]["default"] == "deny"
                        || truthy(&layer["third_party"]["deny"])
                })
        }),
        "ARC005" => Condition::Config(|config| {
            let architecture = &config["architecture"];
            truthy(&architecture["shared"])
                || architecture["layers"]
                    .as_object()
                    .into_iter()
                    .flatten()
                    .any(|(_, layer)| truthy(&layer["modules"]))
        }),
        "ARC006" => Condition::Config(|config| {
            truthy(&config["architecture"]["composition_root"])
                || truthy(&config["architecture"]["wiring"])
        }),
        "ARC007" => Condition::Config(|config| {
            contracts(config).any(|contract| is_set(&contract["public"]))
        }),
        "IMP001" => Condition::Config(|config| {
            contracts(config).any(|contract| is_set(&contract["min_imports_to_bundle"]))
        }),
        "ARC008" => Condition::Config(|config| {
            contracts(config).any(|contract| is_set(&contract["min_importers_to_export"]))
        }),
        "IMP002" => Condition::Option(is_set),
        "ARC009" => Condition::Config(|config| truthy(&config["architecture"]["members"])),
        "ARC010" => Condition::Config(|config| config["project"]["workspace"] == "uv"),
        "LAY001" => Condition::Config(|config| truthy(&config["__structure__"]["forbidden_names"])),
        "ARC014" => Condition::Config(|config| {
            contracts(config)
                .any(|contract| truthy(&contract["required"]) || truthy(&contract["public"]))
        }),
        "IMP003" => Condition::Option(is_set),
        "LAY002" => Condition::Option(|value: &Value| value == "forbid" || value == "require"),
        "TYP001" => Condition::Option(|value: &Value| value == "forbid" || value == "when-needed"),
        "TYP002" => Condition::Option(|value: &Value| value == "when-needed"),
        "LAY003" => Condition::Option(|value: &Value| value == "forbid"),
        "IDM002" => {
            Condition::Option(|value: &Value| !value.is_null() && value["style"] == "match")
        }
        "VIS004" => Condition::Option(is_set),
        "VIS005" => Condition::Option(is_set),
        "CPX001" => Condition::Option(is_set),
        "CPX002" => Condition::Option(is_set),
        "MOD004" => Condition::Option(is_set),
        "MOD006" => Condition::Option(is_set),
        "IMP004" => Condition::Option(truthy),
        "MOD007" => Condition::Option(is_set),
        "MOD008" => Condition::Option(is_set),
        "VIS008" => Condition::Option(|value: &Value| value == "all" || value == "alias"),
        "TST001" => Condition::Config(|config| config["tests"]["layout"] == "mirror"),
        "TST003" => Condition::Config(|config| truthy(&config["tests"]["forbidden_mock_paths"])),
        "TST005" => Condition::Config(max_patches_per_test),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::{Condition, condition};
    use crate::checker::CATALOG;
    use crate::command::policy::Catalog;

    #[test]
    fn every_catalog_activation_has_a_condition_of_its_kind() {
        let catalog = Catalog::load(CATALOG).expect("the generated catalog loads");

        let mismatched: Vec<&str> = catalog
            .rules
            .iter()
            .filter(|rule| {
                let kind = condition(&rule.code)
                    .map(|condition| matches!(condition, Condition::Option(_)));
                let declared = rule
                    .activation
                    .as_ref()
                    .map(|metadata| metadata.option.is_some());
                kind != declared
            })
            .map(|rule| rule.code.as_str())
            .collect();

        assert!(mismatched.is_empty(), "{mismatched:?}");
    }
}
