//! Package contracts resolved against the discovered inventory.
use super::config::{Contract, Policy, Request};
use super::files::FileIndex;
use super::patterns::{ModulePattern, is_within};

pub struct ResolvedContract<'a> {
    pub name: &'a str,
    pub package: &'a str,
    pub spec: &'a Contract,
}

/// Where a limit came from, as findings name it.
pub struct LimitOrigin<'a> {
    pub contract: &'a str,
    pub label: String,
}

pub struct Contracts<'a> {
    pub resolved: Vec<ResolvedContract<'a>>,
    project: Policy,
    defaults: Policy,
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
            project: request.project_policy,
            defaults: request.defaults,
        })
    }

    /// The effective positional limit; no origin means the built-in default.
    pub fn max_positional(&self, module: &str) -> (usize, Option<LimitOrigin<'a>>) {
        // Like Python's max(), the first of equally close contracts wins.
        let mut closest: Option<&ResolvedContract<'a>> = None;
        for contract in &self.resolved {
            if contract.spec.policy.max_positional_parameters.is_none()
                || !is_within(module, contract.package)
            {
                continue;
            }
            let rank = (contract.package.len(), contract.spec.style_override);
            if closest.is_none_or(|best| rank > (best.package.len(), best.spec.style_override)) {
                closest = Some(contract);
            }
        }
        if let Some(contract) = closest
            && let Some(limit) = contract.spec.policy.max_positional_parameters
        {
            let origin = LimitOrigin {
                contract: contract.name,
                label: format!("contract \"{}\"", contract.name),
            };
            return (limit, Some(origin));
        }
        if let Some(limit) = self.project.max_positional_parameters {
            let origin = LimitOrigin {
                contract: "style",
                label: "style".to_owned(),
            };
            return (limit, Some(origin));
        }
        (self.defaults.max_positional_parameters.unwrap_or(5), None)
    }
}
