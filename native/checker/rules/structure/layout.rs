//! ARC011 and ARC012: code outside the declared layers and roles.
use super::{StructureContext, StructureFinding, last_segment};
use crate::checker::analysis::architecture::{ArchitectureModel, ModuleKind};
use serde_json::{Value, json};
use std::collections::HashSet;

pub fn check_unknown_layers(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let model = context.model;
    let paths: Vec<Vec<&str>> = model
        .config
        .layers
        .iter()
        .filter_map(|layer| layer.path.as_deref())
        .map(|path| path.split('.').collect())
        .collect();
    let layers: Vec<&str> = model
        .config
        .layers
        .iter()
        .map(|layer| layer.name.as_str())
        .collect();
    let walk = LayerWalk { context, layers };
    let mut found = Vec::new();
    for container in model.grid_containers() {
        let prefix = format!("{container}.");
        let mut scoped = paths.clone();
        scoped.extend(
            model
                .config
                .module_bindings()
                .filter_map(|(binding, _)| binding.strip_prefix(prefix.as_str()))
                .map(|rest| rest.split('.').collect::<Vec<&str>>()),
        );
        walk.children(&container, &container, &scoped, &mut found);
    }
    found
}

struct LayerWalk<'c, 'a> {
    context: &'c StructureContext<'a>,
    layers: Vec<&'c str>,
}

impl LayerWalk<'_, '_> {
    fn children(
        &self,
        container: &str,
        parent: &str,
        paths: &[Vec<&str>],
        found: &mut Vec<StructureFinding>,
    ) {
        let model = self.context.model;
        if model.has_features() {
            self.loose_modules(parent, found);
        }
        let prefix = format!("{parent}.");
        for package in &self.context.files.packages {
            let Some(child) = package.strip_prefix(prefix.as_str()) else {
                continue;
            };
            if child.contains('.') {
                continue;
            }
            let info = model.info(package);
            if info.as_ref().is_some_and(|info| {
                info.wiring
                    || info.central
                    || matches!(info.kind, ModuleKind::Shared | ModuleKind::CompositionRoot)
            }) {
                continue;
            }
            let rests: Vec<Vec<&str>> = paths
                .iter()
                .filter(|path| path[0] == child)
                .map(|path| path[1..].to_vec())
                .collect();
            if rests.iter().any(Vec::is_empty) {
                continue;
            }
            if !rests.is_empty() {
                self.children(container, package, &rests, found);
                continue;
            }
            let feature = info.as_ref().and_then(|info| info.feature.clone());
            let owner = feature.as_deref().unwrap_or(container);
            found.push(StructureFinding {
                message: format!(
                    "{owner} contains package {child}, which is not a declared layer ({})",
                    self.joined()
                ),
                path: self.location(package),
                source_module: Some(package.clone()),
                expected: json!({"layers": self.layers}),
                hint: format!(
                    "Move the modules of {} into a layer, or assign the package to the intended \
                     layer.",
                    model.display_path(package, true)
                ),
                feature,
                ..StructureFinding::default()
            });
        }
    }

    fn loose_modules(&self, parent: &str, found: &mut Vec<StructureFinding>) {
        let model = self.context.model;
        for (name, source) in &self.context.files.sources {
            if source.is_package || name.rsplit_once('.').map(|(head, _)| head) != Some(parent) {
                continue;
            }
            let Some(info) = model.info(name) else {
                continue;
            };
            if info.layer.is_some()
                || info.wiring
                || matches!(info.kind, ModuleKind::Shared | ModuleKind::CompositionRoot)
            {
                continue;
            }
            let owner = info.feature.as_deref().unwrap_or(parent);
            found.push(StructureFinding {
                message: format!(
                    "{owner} contains module {}.py, which is in no layer ({})",
                    last_segment(name),
                    self.joined()
                ),
                path: source.path.clone(),
                source_module: Some(name.clone()),
                feature: info.feature.clone(),
                expected: json!({"layers": self.layers}),
                hint: format!(
                    "Move {} into a layer of {owner}, such as infrastructure for configuration \
                     and external services.",
                    model.display_path(name, false)
                ),
                ..StructureFinding::default()
            });
        }
    }

    fn joined(&self) -> String {
        if self.layers.is_empty() {
            "(nothing)".to_owned()
        } else {
            self.layers.join(", ")
        }
    }

    fn location(&self, package: &str) -> String {
        self.context
            .files
            .path_for_module(package)
            .unwrap_or(package)
            .to_owned()
    }
}

pub fn check_unclassified(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let model = context.model;
    if model.config.layers.is_empty() && !model.has_features() {
        return Vec::new();
    }
    // A root package with nothing classified is a library outside the architecture.
    let mut described: HashSet<&str> = HashSet::new();
    for name in context.files.sources.keys() {
        if model
            .info(name)
            .is_some_and(|info| info.kind != ModuleKind::Unclassified || info.wiring)
            && let Some(root) = name.split('.').next()
        {
            described.insert(root);
        }
    }
    let classified = model.classified_roots();
    let mut found = Vec::new();
    for (name, source) in &context.files.sources {
        let Some(info) = model.info(name) else {
            continue;
        };
        if source.is_package || info.kind != ModuleKind::Unclassified || info.wiring {
            continue;
        }
        let root = name.split('.').next().unwrap_or(name);
        if !described.contains(root) {
            continue;
        }
        let package = top_package(model, name, &classified);
        found.push(StructureFinding {
            message: format!("{name} is not part of a feature, layer, shared or composition root"),
            path: source.path.clone(),
            source_module: Some(name.clone()),
            expected: Value::Null,
            hint: format!(
                "Give {package} a layer by adding it to that layer's modules list, or list it in \
                 shared or composition_root."
            ),
            ..StructureFinding::default()
        });
    }
    found
}

/// The package directly below the root package, or the module itself.
fn top_package(model: &ArchitectureModel<'_>, module: &str, classified: &HashSet<&str>) -> String {
    let parts: Vec<&str> = module.split('.').collect();
    if !classified.contains(parts[0]) {
        return parts[0].to_owned();
    }
    if parts.len() > 2 && model.roots().iter().any(|root| root == parts[0]) {
        let candidate = parts[..2].join(".");
        if model.is_package(&candidate) {
            return candidate;
        }
    }
    module.to_owned()
}
