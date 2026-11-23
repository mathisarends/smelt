//! Package contracts resolved against the discovered inventory.
use super::config::{Contract, Policy, Request};
use super::files::FileIndex;
use super::patterns::{ModulePattern, is_within};
use serde_json::Value;

const PROJECT_POLICY: &str = "style";
const DEFAULT_MAX_POSITIONAL_PARAMETERS: usize = 5;

pub struct ResolvedContract<'a> {
    pub name: &'a str,
    pub package: &'a str,
    pub spec: &'a Contract,
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
