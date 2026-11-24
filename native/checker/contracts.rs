//! Package contracts resolved against the discovered inventory.
use super::config::errors::ConfigError;
use super::config::{Contract, Policy, Request};
use super::files::FileIndex;
use super::patterns::{ModulePattern, is_within};
use serde_json::Value;
use std::collections::BTreeMap;

const PROJECT_POLICY: &str = "style";
const DEFAULT_MAX_POSITIONAL_PARAMETERS: usize = 5;

pub struct ResolvedContract<'a> {
    pub name: &'a str,
    pub package: &'a str,
    pub spec: &'a Contract,
}

impl ResolvedContract<'_> {
    /// The module a contract entry names, relative to the package.
    pub fn qualify(&self, relative: &str) -> String {
        if relative == "." {
            self.package.to_owned()
        } else {
            format!("{}.{relative}", self.package)
        }
    }

    /// The qualified public entry points; `None` leaves the package unrestricted.
    pub fn public(&self) -> Option<Vec<String>> {
        self.spec
            .public
            .as_ref()
            .map(|entries| entries.iter().map(|entry| self.qualify(entry)).collect())
    }

    /// Whether callers outside the package may import `module`.
    pub fn exposes(&self, module: &str) -> bool {
        self.public().is_none_or(|public| {
            public.iter().any(|pattern| {
                ModulePattern::new(pattern).is_ok_and(|pattern| pattern.matches(module))
            })
        })
    }

    fn wildcards(&self) -> usize {
        self.spec
            .pattern
            .split('.')
            .filter(|segment| *segment == "*")
            .count()
    }
}

/// A package facade threshold, chosen per package by `facade_policies`.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum FacadePolicy {
    Bundle,
    Export,
}

impl FacadePolicy {
    fn name(self) -> &'static str {
        match self {
            Self::Bundle => "min_imports_to_bundle",
            Self::Export => "min_importers_to_export",
        }
    }

    fn value(self, contract: &Contract) -> Option<usize> {
        match self {
            Self::Bundle => contract.min_imports_to_bundle,
            Self::Export => contract.min_importers_to_export,
        }
    }
}

/// Where a policy option came from, as findings name it.
pub struct PolicyOrigin<'a> {
    pub contract: &'a str,
    pub label: String,
    pub value: &'a Value,
}

pub struct Contracts<'a> {
    pub resolved: Vec<ResolvedContract<'a>>,
    project: &'a Policy,
}

impl<'a> Contracts<'a> {
    pub fn resolve(request: &'a Request, files: &'a FileIndex) -> Result<Self, String> {
        let mut resolved = Vec::new();
        for (name, spec) in &request.contracts {
            let pattern = ModulePattern::new(&spec.pattern)?;
            for package in &files.packages {
                if pattern.matches(package) {
                    resolved.push(ResolvedContract {
                        name,
                        package,
                        spec,
                    });
                }
            }
        }
        Ok(Self {
            resolved,
            project: &request.project_policy,
        })
    }

    /// One threshold per concrete package; more literal match segments win.
    pub fn facade_policies(
        &self,
        policy: FacadePolicy,
    ) -> Result<Vec<&ResolvedContract<'a>>, ConfigError> {
        let mut candidates: BTreeMap<&str, Vec<&ResolvedContract<'a>>> = BTreeMap::new();
        for contract in &self.resolved {
            if policy.value(contract.spec).is_some() {
                candidates
                    .entry(contract.package)
                    .or_default()
                    .push(contract);
            }
        }
        let mut chosen = Vec::new();
        for (package, matches) in candidates {
            let rank = matches
                .iter()
                .map(|contract| contract.wildcards())
                .min()
                .unwrap_or(0);
            let mut winners = matches
                .into_iter()
                .filter(|contract| contract.wildcards() == rank);
            let Some(first) = winners.next() else {
                continue;
            };
            for other in winners {
                if policy.value(first.spec) != policy.value(other.spec)
                    || (policy == FacadePolicy::Export
                        && first.spec.count_tests != other.spec.count_tests)
                {
                    let name = policy.name();
                    return Err(ConfigError::one(
                        &format!("architecture.packages.{}.{name}", other.name),
                        format!(
                            "conflicting {name} policies for {package} with equally specific matches"
                        ),
                        "smelt configuration",
                    ));
                }
            }
            chosen.push(first);
        }
        Ok(chosen)
    }

    /// The closest contract setting `option`, else the project-wide style.
    pub fn policy_for(&self, module: &str, option: &str) -> Option<PolicyOrigin<'a>> {
        // Like Python's max(), the first of equally close contracts wins.
        let mut closest: Option<(&ResolvedContract<'a>, &'a Value)> = None;
        for contract in &self.resolved {
            let Some(value) = contract.spec.policy.option(option) else {
                continue;
            };
            if !is_within(module, contract.package) {
                continue;
            }
            let rank = (contract.package.len(), contract.spec.style_override);
            if closest.is_none_or(|(best, _)| rank > (best.package.len(), best.spec.style_override))
            {
                closest = Some((contract, value));
            }
        }
        if let Some((contract, value)) = closest {
            return Some(PolicyOrigin {
                contract: contract.name,
                label: format!("contract \"{}\"", contract.name),
                value,
            });
        }
        self.project.option(option).map(|value| PolicyOrigin {
            contract: PROJECT_POLICY,
            label: PROJECT_POLICY.to_owned(),
            value,
        })
    }

    /// The effective limit of a policy option, else its built-in default.
    pub fn threshold(&self, module: &str, option: &str, default: usize) -> usize {
        self.policy_for(module, option)
            .and_then(|origin| origin.value.as_u64())
            .and_then(|limit| usize::try_from(limit).ok())
            .unwrap_or(default)
    }

    /// The effective positional limit; no origin means the built-in default.
    pub fn max_positional(&self, module: &str) -> (usize, Option<PolicyOrigin<'a>>) {
        match self.policy_for(module, "max_positional_parameters") {
            Some(origin) => {
                let limit = origin
                    .value
                    .as_u64()
                    .and_then(|limit| usize::try_from(limit).ok())
                    .unwrap_or(DEFAULT_MAX_POSITIONAL_PARAMETERS);
                (limit, Some(origin))
            }
            None => (DEFAULT_MAX_POSITIONAL_PARAMETERS, None),
        }
    }
}
