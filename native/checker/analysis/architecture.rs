//! Where every module sits in the configured architecture, as in `smelt.model`.
use crate::checker::config::architecture::Architecture;
use crate::checker::files::FileIndex;
use crate::checker::patterns::is_within;
use std::borrow::Cow;
use std::collections::{BTreeMap, HashMap, HashSet};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ModuleKind {
    Feature,
    Shared,
    CompositionRoot,
    Unclassified,
}

/// `Feature` covers every module in the feature/layer grid. Without configured
/// features the grid spans the root packages and `feature` is `None`.
#[derive(Clone)]
pub struct ModuleInfo {
    pub name: String,
    pub kind: ModuleKind,
    pub feature: Option<String>,
    pub layer: Option<String>,
    pub wiring: bool,
    /// Outside the features, but in a layer through an explicit module binding.
    pub central: bool,
}

impl ModuleInfo {
    pub fn in_grid(&self) -> bool {
        self.kind == ModuleKind::Feature
    }
}

/// How one architecture shares a uv workspace with its members.
#[derive(Clone, Copy)]
pub enum Scope<'a> {
    /// The project has one architecture.
    Project,
    /// The whole workspace: the root architecture over these top-level packages,
    /// and every other module as the part that owns it classifies it.
    Combined(&'a [String]),
    /// One architecture judging its own top-level packages.
    Part(&'a [String]),
}

pub struct ArchitectureModel<'a> {
    pub config: &'a Architecture,
    roots: &'a [String],
    first_party: &'a [String],
    scope: Scope<'a>,
    packages: HashSet<&'a str>,
    modules: HashMap<&'a str, ModuleInfo>,
    features: BTreeMap<String, String>,
    classifier: Classifier<'a>,
    /// The scoped architectures of a combined model, the root first.
    parts: Vec<ArchitectureModel<'a>>,
}

impl<'a> ArchitectureModel<'a> {
    pub fn build(
        config: &'a Architecture,
        roots: &'a [String],
        files: &'a FileIndex,
        scope: Scope<'a>,
    ) -> Self {
        let packages: HashSet<&str> = files.packages.iter().map(String::as_str).collect();
        let classified = match scope {
            Scope::Project => None,
            Scope::Combined(packages) | Scope::Part(packages) => Some(packages),
        };
        let classifier = Classifier::new(config, roots, classified, packages.clone());
        let mut modules = HashMap::with_capacity(files.sources.len());
        let mut features = BTreeMap::new();
        for name in files.sources.keys() {
            let info = classifier.classify(name);
            if let Some(feature) = &info.feature
                && !features.contains_key(feature)
                && let Some(package) = classifier.feature_package(name)
            {
                features.insert(feature.clone(), package);
            }
            modules.insert(name.as_str(), info);
        }
        Self {
            config,
            roots: match scope {
                Scope::Part(packages) => packages,
                Scope::Project | Scope::Combined(_) => roots,
            },
            first_party: roots,
            scope,
            packages,
            modules,
            features,
            classifier,
            parts: Vec::new(),
        }
    }

    /// One view over the scoped architectures of a workspace; the first part is the root.
    pub fn combine(
        config: &'a Architecture,
        roots: &'a [String],
        files: &'a FileIndex,
        root_packages: &'a [String],
        parts: Vec<Self>,
    ) -> Self {
        let mut combined = Self::build(config, roots, files, Scope::Combined(root_packages));
        combined.modules.clear();
        combined.features.clear();
        for part in &parts {
            combined.modules.extend(
                part.modules
                    .iter()
                    .filter(|(name, _)| part.owns(name))
                    .map(|(name, info)| (*name, info.clone())),
            );
            for (feature, package) in &part.features {
                combined
                    .features
                    .entry(feature.clone())
                    .or_insert_with(|| package.clone());
            }
        }
        combined.parts = parts;
        combined
    }

    /// The scoped architectures of a combined model, the root first.
    pub fn parts(&self) -> &[Self] {
        &self.parts
    }

    fn owns(&self, module: &str) -> bool {
        match self.scope {
            Scope::Part(packages) => {
                let top = module.split('.').next().unwrap_or_default();
                packages.iter().any(|package| package == top)
            }
            Scope::Project | Scope::Combined(_) => true,
        }
    }

    /// The scoped architecture of `module`; the model itself if not combined.
    pub fn part_for(&self, module: &str) -> &Self {
        self.parts
            .iter()
            .find(|part| part.owns(module))
            .unwrap_or(self)
    }

    pub fn info(&self, module: &str) -> Option<Cow<'_, ModuleInfo>> {
        if let Some(info) = self.modules.get(module) {
            return Some(Cow::Borrowed(info));
        }
        if !self.is_first_party(module) {
            return None;
        }
        if !self.parts.is_empty() {
            let part = self.part_for(module);
            if !std::ptr::eq(part, self) {
                return part.info(module);
            }
            return Some(Cow::Owned(ModuleInfo {
                name: module.to_owned(),
                kind: ModuleKind::Unclassified,
                feature: None,
                layer: None,
                wiring: false,
                central: false,
            }));
        }
        Some(Cow::Owned(self.classifier.classify(module)))
    }

    pub fn has_features(&self) -> bool {
        self.config.features.is_some()
    }

    pub fn roots(&self) -> &[String] {
        self.roots
    }

    pub fn is_package(&self, module: &str) -> bool {
        self.packages.contains(module)
    }

    pub fn is_first_party(&self, module: &str) -> bool {
        self.first_party.iter().any(|root| is_within(module, root))
    }

    /// The root packages holding at least one module with an architecture role.
    pub fn classified_roots(&self) -> HashSet<&str> {
        self.modules
            .iter()
            .filter(|(_, info)| info.kind != ModuleKind::Unclassified)
            .filter_map(|(name, _)| name.split('.').next())
            .collect()
    }

    /// A short path for hints, relative to the feature container or root package.
    pub fn display_path(&self, module: &str, package: bool) -> String {
        // A workspace module is shown under the architecture that owns it.
        let part = self.part_for(module);
        let spec = part.config.features.as_ref();
        let container = spec.and_then(|spec| {
            spec.root.clone().or_else(|| {
                let pattern = spec.pattern.as_deref()?;
                Some(
                    pattern
                        .rsplit_once('.')
                        .map_or(pattern, |(head, _)| head)
                        .to_owned(),
                )
            })
        });
        let inside = container
            .as_deref()
            .and_then(|prefix| module.strip_prefix(prefix)?.strip_prefix('.'));
        let shown = match (inside, spec) {
            (Some(rest), _) => rest,
            (None, None) => match module.split_once('.') {
                Some((root, rest)) if part.roots.iter().any(|item| item == root) => rest,
                _ => module,
            },
            (None, Some(_)) => module,
        };
        format!(
            "{}{}",
            shown.replace('.', "/"),
            if package { "/" } else { ".py" }
        )
    }

    pub fn feature_packages(&self) -> impl Iterator<Item = &str> {
        self.features.values().map(String::as_str)
    }

    pub fn feature_package_for(&self, feature: &str) -> String {
        if let Some(package) = self.features.get(feature) {
            return package.clone();
        }
        let spec = self.config.features.as_ref();
        if let Some(root) = spec.and_then(|spec| spec.root.as_ref()) {
            return format!("{root}.{feature}");
        }
        if let Some(pattern) = spec.and_then(|spec| spec.pattern.as_ref()) {
            return pattern.replace("{feature}", feature);
        }
        feature.to_owned()
    }

    /// Packages whose direct child packages must be declared layers.
    pub fn grid_containers(&self) -> Vec<String> {
        if self.config.layers.is_empty() {
            return Vec::new();
        }
        if self.has_features() {
            let mut packages: Vec<String> = self.features.values().cloned().collect();
            packages.sort();
            return packages;
        }
        self.roots
            .iter()
            .filter(|root| self.packages.contains(root.as_str()))
            .cloned()
            .collect()
    }

    /// The part of the architecture `module` belongs to: `user.domain`, a central
    /// package, a shared or composition root entry, else the module.
    pub fn node(&self, module: &str) -> String {
        let Some(info) = self.info(module) else {
            return module.to_owned();
        };
        if info.in_grid() && (info.feature.is_some() || info.layer.is_some()) {
            let parts: Vec<&str> = [info.feature.as_deref(), info.layer.as_deref()]
                .into_iter()
                .flatten()
                .collect();
            return parts.join(".");
        }
        let config = self.part_for(module).config;
        let entries: Vec<&str> = match info.kind {
            ModuleKind::Shared => config.shared.iter().map(String::as_str).collect(),
            ModuleKind::CompositionRoot => {
                config.composition_root.iter().map(String::as_str).collect()
            }
            _ if info.central => config.module_bindings().map(|(prefix, _)| prefix).collect(),
            _ => Vec::new(),
        };
        let mut longest: Option<&str> = None;
        for entry in entries {
            if is_within(module, entry) && longest.is_none_or(|best| entry.len() > best.len()) {
                longest = Some(entry);
            }
        }
        longest.unwrap_or(module).to_owned()
    }
}

struct Classifier<'a> {
    config: &'a Architecture,
    roots: &'a [String],
    scope: Option<&'a [String]>,
    packages: HashSet<&'a str>,
    central: HashMap<&'a str, &'a str>,
    layer_paths: Vec<(Vec<&'a str>, &'a str)>,
    feature_prefix: Option<Vec<&'a str>>,
}

impl<'a> Classifier<'a> {
    /// A classifier for `roots`; with a `scope`, only for those top-level packages.
    fn new(
        config: &'a Architecture,
        roots: &'a [String],
        scope: Option<&'a [String]>,
        packages: HashSet<&'a str>,
    ) -> Self {
        let mut layer_paths: Vec<(Vec<&str>, &str)> = config
            .layers
            .iter()
            .filter_map(|layer| {
                let path = layer.path.as_deref()?;
                Some((path.split('.').collect(), layer.name.as_str()))
            })
            .collect();
        layer_paths.sort_by_key(|(path, _)| std::cmp::Reverse(path.len()));
        let feature_prefix = config.features.as_ref().and_then(|features| {
            if let Some(root) = &features.root {
                return Some(root.split('.').collect());
            }
            let pattern: Vec<&str> = features.pattern.as_deref()?.split('.').collect();
            Some(pattern[..pattern.len().saturating_sub(1)].to_vec())
        });
        Self {
            config,
            roots: scope.unwrap_or(roots),
            scope,
            packages,
            central: config.module_bindings().collect(),
            layer_paths,
            feature_prefix,
        }
    }

    fn classify(&self, module: &str) -> ModuleInfo {
        let top = module.split('.').next().unwrap_or_default();
        if self
            .scope
            .is_some_and(|scope| !scope.iter().any(|package| package == top))
        {
            return ModuleInfo {
                name: module.to_owned(),
                kind: ModuleKind::Unclassified,
                feature: None,
                layer: None,
                wiring: false,
                central: false,
            };
        }
        let config = self.config;
        let wiring = config.wiring.iter().any(|item| item.matches(module));
        let info = |kind, feature: Option<&str>, layer: Option<&str>, central| ModuleInfo {
            name: module.to_owned(),
            kind,
            feature: feature.map(str::to_owned),
            layer: layer.map(str::to_owned),
            wiring,
            central,
        };
        if config
            .composition_root
            .iter()
            .any(|root| is_within(module, root))
        {
            return info(ModuleKind::CompositionRoot, None, None, false);
        }
        if config.shared.iter().any(|entry| is_within(module, entry)) {
            return info(ModuleKind::Shared, None, None, false);
        }
        if let Some(layer) = self.central_layer(module) {
            return info(ModuleKind::Feature, None, Some(layer), true);
        }
        if config.features.is_none() {
            let layer = self.root_layer(module);
            let kind = if layer.is_some() {
                ModuleKind::Feature
            } else {
                ModuleKind::Unclassified
            };
            return info(kind, None, layer, false);
        }
        let segments: Vec<&str> = module.split('.').collect();
        let Some(index) = self.feature_index(&segments) else {
            return info(ModuleKind::Unclassified, None, None, false);
        };
        let layer = self.match_layer(&segments[index + 1..]);
        info(ModuleKind::Feature, Some(segments[index]), layer, false)
    }

    fn central_layer(&self, module: &str) -> Option<&'a str> {
        let mut prefix = module;
        loop {
            if let Some(layer) = self.central.get(prefix) {
                return Some(layer);
            }
            prefix = prefix.rsplit_once('.')?.0;
        }
    }

    /// Without features, layers sit directly in a root package.
    fn root_layer(&self, module: &str) -> Option<&'a str> {
        let rest = self.roots.iter().find_map(|root| {
            module
                .strip_prefix(root.as_str())
                .and_then(|rest| rest.strip_prefix('.'))
        })?;
        let segments: Vec<&str> = rest.split('.').collect();
        self.match_layer(&segments)
    }

    fn feature_package(&self, module: &str) -> Option<String> {
        let segments: Vec<&str> = module.split('.').collect();
        let index = self.feature_index(&segments)?;
        Some(segments[..=index].join("."))
    }

    /// The index of the feature segment when `segments` lie in a feature package.
    fn feature_index(&self, segments: &[&str]) -> Option<usize> {
        let prefix = self.feature_prefix.as_ref()?;
        let index = prefix.len();
        if segments.len() <= index || segments[..index] != prefix[..] {
            return None;
        }
        self.packages
            .contains(segments[..=index].join(".").as_str())
            .then_some(index)
    }

    fn match_layer(&self, rest: &[&str]) -> Option<&'a str> {
        self.layer_paths
            .iter()
            .find(|(path, _)| rest.starts_with(path))
            .map(|(_, name)| *name)
    }
}
