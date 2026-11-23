//! Layer, feature, third-party, shared and composition-root boundaries.
use super::{BoundaryImport, GraphContext, ImportFinding, import_violation, join, skip_import};
use crate::checker::analysis::architecture::{ModuleInfo, ModuleKind};
use crate::checker::analysis::imports::ImportDetail;
use crate::checker::analysis::stdlib::is_stdlib;
use crate::checker::config::architecture::ThirdParty;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::{ImportLink, Violation};
use serde_json::{Value, json};
use std::collections::HashSet;

const SHARED_MAY_IMPORT: &str = "shared code and third-party packages, no feature";
const CENTRAL_MAY_IMPORT: &str =
    "shared code, central modules of its layers and third-party packages, no feature";

pub fn check_layers(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
    imports: &[BoundaryImport<'_>],
) -> Vec<Violation> {
    let model = context.model;
    let mut found = Vec::new();
    for item in imports {
        let detail = item.detail.as_ref();
        if detail.external || skip_import(context, detail) {
            continue;
        }
        let Some(source) = model.info(&detail.importer) else {
            continue;
        };
        let target = model.info(&detail.imported);
        let Some(target_layer) = forbidden_layer(context, &source, target.as_deref()) else {
            continue;
        };
        let layer = source.layer.as_deref().unwrap_or_default();
        let allowed = may_depend_on(context, layer);
        let via = if item.chain.is_empty() {
            ""
        } else {
            " (indirectly)"
        };
        found.push(import_violation(
            rule,
            context,
            detail,
            ImportFinding {
                message: format!("{layer} must not depend on {target_layer}{via}"),
                expected: json!({"may_depend_on": allowed}),
                hint: Some(layer_hint(layer, &allowed, &item.chain)),
                chain: item.chain.clone(),
                edge: None,
            },
        ));
    }
    found
}

fn may_depend_on(context: &GraphContext<'_>, layer: &str) -> Vec<String> {
    context
        .model
        .config
        .layer(layer)
        .map(|layer| layer.may_depend_on.clone())
        .unwrap_or_default()
}

/// The target's layer if `source` must not import `target`.
fn forbidden_layer<'t>(
    context: &GraphContext<'_>,
    source: &ModuleInfo,
    target: Option<&'t ModuleInfo>,
) -> Option<&'t str> {
    let target = target?;
    if source.wiring || !(source.in_grid() && target.in_grid()) {
        return None;
    }
    let source_layer = source.layer.as_deref()?;
    let target_layer = target.layer.as_deref()?;
    if source_layer == target_layer {
        return None;
    }
    // Across features ARC002 decides; a central module shares its layer rules with all.
    if source.feature != target.feature && !(source.central || target.central) {
        return None;
    }
    if may_depend_on(context, source_layer)
        .iter()
        .any(|layer| layer == target_layer)
    {
        return None;
    }
    Some(target_layer)
}

fn layer_hint(layer: &str, allowed: &[String], chain: &[ImportLink]) -> String {
    if allowed.is_empty() && chain.is_empty() {
        return format!(
            "{layer} depends on no other layer. Define what it needs as an abstraction in \
             {layer} and implement it in an outer layer."
        );
    }
    let allowed = join(allowed);
    if let Some((_, through)) = chain.split_last() {
        let through: Vec<String> = through.iter().map(|link| link.imported.clone()).collect();
        return format!(
            "The dependency goes through {}; break the chain or move the code into a layer \
             {layer} may use ({allowed}).",
            join(&through)
        );
    }
    format!(
        "Move what {layer} needs into a layer it may depend on ({allowed}), or invert the \
         dependency behind an abstraction."
    )
}

pub fn check_cross_feature(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
    imports: &[BoundaryImport<'_>],
) -> Vec<Violation> {
    let config = context.model.config;
    let cross = &config.cross_feature;
    if !cross.deny_by_default {
        return Vec::new();
    }
    let mut found = Vec::new();
    for item in imports {
        let detail = item.detail.as_ref();
        if detail.external || skip_import(context, detail) {
            continue;
        }
        let (Some(source), Some(target)) = (
            context.model.info(&detail.importer),
            context.model.info(&detail.imported),
        ) else {
            continue;
        };
        if source.wiring || !(source.in_grid() && target.in_grid()) {
            continue;
        }
        let (Some(source_feature), Some(target_feature)) =
            (source.feature.as_deref(), target.feature.as_deref())
        else {
            continue;
        };
        if source_feature == target_feature {
            continue;
        }
        let layers = source.layer.as_deref().zip(target.layer.as_deref());
        if layers.is_some_and(|(source_layer, target_layer)| {
            cross.allows(
                (source_feature, source_layer),
                (target_feature, target_layer),
            )
        }) {
            continue;
        }
        let pair = format!(
            "{} -> {}",
            source.layer.as_deref().unwrap_or("(feature root)"),
            target.layer.as_deref().unwrap_or("(feature root)")
        );
        let allowed = cross.labels();
        let mut hints = Vec::new();
        if !allowed.is_empty() {
            hints.push(format!("Use an allowed pair ({}).", join(&allowed)));
        }
        if let Some(shared) = config.shared.first() {
            hints.push(format!("Move the shared concept into {shared}."));
        }
        if let Some((source_layer, target_layer)) = layers {
            hints.push(format!(
                "If {source_feature} may rely on {target_feature}, that is a policy decision \
                 rather than a bug: allow it under architecture.cross_feature.allow as {{from: \
                 {source_feature}.{source_layer}, to: {target_feature}.{target_layer}}}."
            ));
        }
        found.push(import_violation(
            rule,
            context,
            detail,
            ImportFinding {
                message: format!(
                    "feature {source_feature} must not import feature {target_feature} ({pair})"
                ),
                expected: json!({"cross_feature_allow": allowed}),
                hint: (!hints.is_empty()).then(|| hints.join(" ")),
                chain: item.chain.clone(),
                edge: None,
            },
        ));
    }
    found
}

pub fn check_third_party(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
    imports: &[BoundaryImport<'_>],
) -> Vec<Violation> {
    let mut reported: HashSet<(&str, &str)> = HashSet::new();
    let mut found = Vec::new();
    for item in imports {
        let detail = item.detail.as_ref();
        if !detail.external || is_stdlib(&detail.imported) || skip_import(context, detail) {
            continue;
        }
        let Some(source) = context.model.info(&detail.importer) else {
            continue;
        };
        if !source.in_grid() {
            continue;
        }
        let Some(layer) = source.layer.as_deref() else {
            continue;
        };
        let Some(policy) = context.model.config.layer(layer).map(|l| &l.third_party) else {
            continue;
        };
        let names = imported_names(detail);
        if !policy.denies(&names) {
            continue;
        }
        let key = (detail.importer.as_str(), detail.imported.as_str());
        if !item.chain.is_empty() && reported.contains(&key) {
            continue;
        }
        reported.insert(key);
        let mut message = third_party_message(layer, detail, policy);
        if !item.chain.is_empty() {
            message.push_str(" (indirectly)");
        }
        found.push(import_violation(
            rule,
            context,
            detail,
            ImportFinding {
                message,
                expected: third_party_expected(policy),
                hint: Some(third_party_hint(context, detail, layer)),
                chain: item.chain.clone(),
                edge: None,
            },
        ));
    }
    found
}

fn imported_names(detail: &ImportDetail) -> Vec<&str> {
    if detail.names.is_empty() {
        vec![detail.imported.as_str()]
    } else {
        detail.names.iter().map(String::as_str).collect()
    }
}

fn third_party_message(layer: &str, detail: &ImportDetail, policy: &ThirdParty) -> String {
    let package = detail.top_level();
    if policy.deny_by_default {
        let suffix = if policy.allow.is_empty() {
            format!("{layer} allows no third-party packages")
        } else {
            format!("{layer} allows only: {}", policy.allow.join(", "))
        };
        return format!("{layer} must not import {package} ({suffix})");
    }
    format!(
        "{layer} must not import {package} (denied: {})",
        join(&policy.deny)
    )
}

fn third_party_expected(policy: &ThirdParty) -> Value {
    if policy.deny_by_default {
        json!({"third_party": {"default": "deny", "allow": policy.allow}})
    } else {
        json!({"third_party": {"default": "allow", "deny": policy.deny}})
    }
}

fn third_party_hint(context: &GraphContext<'_>, detail: &ImportDetail, layer: &str) -> String {
    let names = imported_names(detail);
    let welcoming: Vec<String> = context
        .model
        .config
        .layers
        .iter()
        .filter(|candidate| candidate.name != layer && !candidate.third_party.denies(&names))
        .map(|candidate| candidate.name.clone())
        .collect();
    let package = detail.top_level();
    if welcoming.is_empty() {
        return format!("No layer allows {package}; add it to a layer's third_party policy.");
    }
    format!(
        "Keep {package} in a layer that allows it ({}) and let {layer} depend on an abstraction.",
        join(&welcoming)
    )
}

pub fn check_shared(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
    imports: &[BoundaryImport<'_>],
) -> Vec<Violation> {
    let mut found = Vec::new();
    for item in imports {
        let detail = item.detail.as_ref();
        if detail.external || skip_import(context, detail) {
            continue;
        }
        let (Some(source), Some(target)) = (
            context.model.info(&detail.importer),
            context.model.info(&detail.imported),
        ) else {
            continue;
        };
        let shared = source.kind == ModuleKind::Shared;
        if !shared && !source.central {
            continue;
        }
        let Some(feature) = target.feature.as_deref().filter(|_| target.in_grid()) else {
            continue;
        };
        let (kind, may_import) = if shared {
            ("shared", SHARED_MAY_IMPORT)
        } else {
            ("central", CENTRAL_MAY_IMPORT)
        };
        let importer = &detail.importer;
        found.push(import_violation(
            rule,
            context,
            detail,
            ImportFinding {
                message: format!("{kind} module {importer} must not import feature {feature}"),
                expected: json!({"may_import": may_import}),
                hint: Some(format!(
                    "Move what {importer} needs out of {feature} into {kind} code, or move \
                     {importer} into the {feature} feature."
                )),
                chain: item.chain.clone(),
                edge: None,
            },
        ));
    }
    found
}

pub fn check_composition_root(
    rule: &CatalogRule,
    context: &GraphContext<'_>,
    imports: &[BoundaryImport<'_>],
) -> Vec<Violation> {
    let model = context.model;
    let mut found = Vec::new();
    for item in imports {
        let detail = item.detail.as_ref();
        if detail.external || skip_import(context, detail) {
            continue;
        }
        let Some(source) = model.info(&detail.importer) else {
            continue;
        };
        if source.kind == ModuleKind::CompositionRoot || source.wiring {
            continue;
        }
        let Some(target) = model.info(&detail.imported) else {
            continue;
        };
        if target.kind != ModuleKind::CompositionRoot && !target.wiring {
            continue;
        }
        if super::wiring_facade(model, &source, &target) {
            continue;
        }
        let label = if target.wiring {
            "wiring module"
        } else {
            "composition root"
        };
        found.push(import_violation(
            rule,
            context,
            detail,
            ImportFinding {
                message: format!(
                    "{} must not import the {label} {}",
                    detail.importer, detail.imported
                ),
                expected: json!({"composition_root": model.config.composition_root}),
                hint: Some(
                    "The composition root depends on everything; nothing depends on it. Pass \
                     what you need in from the root instead."
                        .to_owned(),
                ),
                chain: item.chain.clone(),
                edge: None,
            },
        ));
    }
    found
}
