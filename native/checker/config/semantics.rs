//! Cross-field policy checks, as in `smelt.config.validation`.
use super::errors::{Issue, did_you_mean};
use super::models::{ARCHITECTURE, SMELT};
use super::schema::{Validator, string_list};
use super::yaml::Yaml;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap};

const NON_LAYOUT: [&str; 4] = ["packages", "canonical_exports", "import_graph", "members"];

/// Validate references with a complete layer namespace even if other keys failed.
pub fn partial_layer_issues(raw: &Yaml) -> Vec<Issue> {
    let mut sections: Vec<(String, Option<&Yaml>)> =
        vec![("architecture".to_owned(), raw.get("architecture"))];
    if let Some(members) = raw.get("members").and_then(Yaml::as_map) {
        for (path, member) in members {
            if member.as_map().is_some() {
                let name = path.as_str().map_or_else(|| path.repr(), str::to_owned);
                sections.push((
                    format!("members.{name}.architecture"),
                    member.get("architecture"),
                ));
            }
        }
    }
    let mut issues = Vec::new();
    for (location, architecture) in sections {
        let Some(architecture) = architecture.filter(|value| value.as_map().is_some()) else {
            continue;
        };
        let Some(layers) = architecture.get("layers").filter(|v| v.as_map().is_some()) else {
            continue;
        };
        let Some(mut partial) = validate_section(&[("layers", layers)]) else {
            continue;
        };
        if let Some(cross) = architecture.get("cross_feature")
            && let Some(wider) = validate_section(&[("layers", layers), ("cross_feature", cross)])
        {
            partial = wider;
        }
        let mut checker = Checker::new(&partial["architecture"], Vec::new(), location);
        checker.check_layers();
        issues.append(&mut checker.errors);
    }
    issues
}

fn validate_section(entries: &[(&str, &Yaml)]) -> Option<Value> {
    let key = |name: &str| Yaml::Str(name.to_owned());
    let section = entries
        .iter()
        .map(|(name, value)| (key(name), (*value).clone()))
        .collect();
    let document = Yaml::Map(vec![
        (key("version"), Yaml::Int(2)),
        (key("project"), Yaml::Map(Vec::new())),
        (key("architecture"), Yaml::Map(section)),
    ]);
    let mut validator = Validator::default();
    validator.model(&SMELT, &document)
}

struct Scope {
    location: String,
    architecture: Value,
    packages: Vec<String>,
}

fn architecture_scopes(config: &Value) -> Vec<Scope> {
    let Some(packages) = config
        .get("__workspace_packages__")
        .and_then(Value::as_object)
        .filter(|packages| !packages.is_empty())
    else {
        return Vec::new();
    };
    let mut scopes = vec![Scope {
        location: "architecture".to_owned(),
        architecture: config["architecture"].clone(),
        packages: string_list(packages.get("")),
    }];
    let mut members: Vec<(&String, &Value)> = packages
        .iter()
        .filter(|(path, _)| !path.is_empty())
        .collect();
    members.sort_by_key(|(path, _)| path.as_str());
    for (path, names) in members {
        let local = config["members"]
            .get(path)
            .and_then(|member| member.get("architecture"))
            .filter(|value| !value.is_null())
            .cloned()
            .unwrap_or_else(default_architecture);
        let mut architecture = local;
        architecture["import_graph"] = config["architecture"]["import_graph"].clone();
        scopes.push(Scope {
            location: format!("members.{path}.architecture"),
            architecture,
            packages: string_list(Some(names)),
        });
    }
    scopes
}

fn default_architecture() -> Value {
    Validator::default()
        .model(&ARCHITECTURE, &Yaml::Map(Vec::new()))
        .unwrap_or_else(|| json!({}))
}

fn has_layout(architecture: &Value) -> bool {
    string_list(architecture.get("__set__"))
        .iter()
        .any(|key| !NON_LAYOUT.contains(&key.as_str()))
}

/// Errors and warnings; each architecture is checked against its own packages.
pub fn validate_semantics(config: &Value) -> (Vec<Issue>, Vec<Issue>) {
    let scopes = architecture_scopes(config);
    let root_packages = string_list(config["project"].get("root_packages"));
    let roots = scopes
        .first()
        .map_or_else(|| root_packages.clone(), |scope| scope.packages.clone());
    let mut root = Checker::new(&config["architecture"], roots, "architecture".to_owned());
    if let Some(packages) = config
        .get("__workspace_packages__")
        .and_then(Value::as_object)
    {
        for (path, names) in packages.iter().filter(|(path, _)| !path.is_empty()) {
            for package in string_list(Some(names)) {
                root.member_owners.insert(package, path.clone());
            }
        }
    }
    root.run();
    root.contracts(config, &root_packages);
    let (mut errors, mut warnings) = (root.errors, root.warnings);
    if scopes
        .first()
        .is_some_and(|scope| scope.packages.is_empty())
        && has_layout(&config["architecture"])
        && errors.is_empty()
    {
        errors.push(Issue::new(
            "architecture",
            "the workspace root has no selected packages; move layers and features to the member configs",
        ));
    }
    if is_nonempty(&config["architecture"]["members"]) && config["project"]["workspace"] != "uv" {
        errors.push(Issue::new(
            "architecture.members",
            "requires project.workspace: uv",
        ));
    }
    for (path, member) in config["members"].as_object().into_iter().flatten() {
        if is_nonempty(&member["architecture"]["members"]) {
            errors.push(Issue::new(
                format!("members.{path}.architecture.members"),
                "member boundaries are configured in the workspace root",
            ));
        }
    }
    for scope in scopes.iter().skip(1) {
        let mut member = Checker::new(
            &scope.architecture,
            scope.packages.clone(),
            scope.location.clone(),
        );
        member.run();
        errors.append(&mut member.errors);
        warnings.append(&mut member.warnings);
    }
    (errors, warnings)
}

fn is_nonempty(value: &Value) -> bool {
    value.as_object().is_some_and(|entries| !entries.is_empty())
}

fn within(module: &str, package: &str) -> bool {
    module
        .strip_prefix(package)
        .is_some_and(|rest| rest.is_empty() || rest.starts_with('.'))
}

#[derive(Clone)]
struct Binding {
    prefix: String,
    layer: String,
    key: String,
}

struct Checker<'a> {
    architecture: &'a Value,
    roots: Vec<String>,
    location: String,
    member_owners: BTreeMap<String, String>,
    errors: Vec<Issue>,
    warnings: Vec<Issue>,
}

impl<'a> Checker<'a> {
    fn new(architecture: &'a Value, roots: Vec<String>, location: String) -> Self {
        Self {
            architecture,
            roots,
            location,
            member_owners: BTreeMap::new(),
            errors: Vec::new(),
            warnings: Vec::new(),
        }
    }

    fn error(&mut self, path: impl Into<String>, message: impl Into<String>) {
        self.errors.push(Issue::new(path, message));
    }

    fn run(&mut self) {
        self.check_layers();
        self.module_sets();
    }

    fn check_layers(&mut self) {
        self.layers();
        self.cross_feature();
    }

    fn contracts(&mut self, config: &Value, roots: &[String]) {
        let structure = &config["__structure__"];
        for (symbol, export) in structure["canonical_exports"]
            .as_object()
            .into_iter()
            .flatten()
        {
            let export = export.as_str().unwrap_or_default();
            if [symbol.as_str(), export]
                .iter()
                .any(|name| !roots.iter().any(|root| root == first(name)))
            {
                self.error(
                    format!("architecture.canonical_exports.{symbol}"),
                    "definition and public export must be inside project.root_packages",
                );
            }
        }
        let overrides = config["style"]["overrides"].as_object();
        for (name, contract) in structure["contracts"].as_object().into_iter().flatten() {
            let pattern = contract["match"].as_str().unwrap_or_default();
            let head = first(pattern);
            if head != "*" && !roots.iter().any(|root| root == head) {
                let policy = if overrides.is_some_and(|entries| entries.contains_key(name)) {
                    "style.overrides"
                } else {
                    "architecture.packages"
                };
                self.error(
                    format!("{policy}.{name}.match"),
                    format!(
                        "\"{pattern}\" is not inside project.root_packages ({})",
                        roots.join(", ")
                    ),
                );
            }
        }
    }

    fn layer_names(&self) -> Vec<String> {
        self.architecture["layers"]
            .as_object()
            .map(|layers| layers.keys().cloned().collect())
            .unwrap_or_default()
    }

    fn layers(&mut self) {
        let architecture = self.architecture;
        let mut seen: HashMap<String, String> = HashMap::new();
        for (name, layer) in architecture["layers"].as_object().into_iter().flatten() {
            let base = format!("{}.layers.{name}", self.location);
            for (index, dependency) in string_list(layer.get("may_depend_on")).iter().enumerate() {
                self.check_layer(&format!("{base}.may_depend_on[{index}]"), dependency);
            }
            let Some(path) = layer["path"].as_str() else {
                continue;
            };
            if let Some(first_owner) = seen.get(path) {
                self.errors.push(Issue::new(
                    format!("{base}.path"),
                    format!("duplicate layer path \"{path}\" (already used by \"{first_owner}\")"),
                ));
            }
            seen.entry(path.to_owned()).or_insert_with(|| name.clone());
        }
        if let Some(cycle) = find_layer_cycle(&architecture["layers"]) {
            self.warnings.push(Issue::new(
                format!("{}.layers", self.location),
                format!("cyclic may_depend_on: {}", cycle.join(" -> ")),
            ));
        }
    }

    fn cross_feature(&mut self) {
        let architecture = self.architecture;
        let allow = architecture["cross_feature"]["allow"].as_array();
        for (index, allowance) in allow.into_iter().flatten().enumerate() {
            let pair = if let Some(text) = allowance.as_str() {
                let (source, target) = text.split_once("->").unwrap_or((text, ""));
                [source.trim().to_owned(), target.trim().to_owned()]
            } else {
                let layer = |key: &str| {
                    allowance[key]
                        .as_str()
                        .and_then(|value| value.split_once('.'))
                        .map_or_else(String::new, |(_, name)| name.to_owned())
                };
                [layer("from"), layer("to")]
            };
            for name in pair {
                let path = format!("{}.cross_feature.allow[{index}]", self.location);
                self.check_layer(&path, &name);
            }
        }
    }

    fn check_layer(&mut self, path: &str, name: &str) {
        let names = self.layer_names();
        if !names.iter().any(|layer| layer == name) {
            self.error(
                path,
                format!("unknown layer \"{name}\"{}", did_you_mean(name, &names)),
            );
        }
    }

    fn bindings(&self) -> Vec<Binding> {
        let mut bindings = Vec::new();
        for (layer, spec) in self.architecture["layers"]
            .as_object()
            .into_iter()
            .flatten()
        {
            for (index, prefix) in string_list(spec.get("modules")).into_iter().enumerate() {
                bindings.push(Binding {
                    prefix,
                    layer: layer.clone(),
                    key: format!("layers.{layer}.modules[{index}]"),
                });
            }
        }
        bindings
    }

    fn module_sets(&mut self) {
        let architecture = self.architecture;
        let location = self.location.clone();
        let mut named: Vec<(String, String)> = Vec::new();
        for key in ["shared", "composition_root"] {
            for (index, module) in string_list(architecture.get(key)).into_iter().enumerate() {
                named.push((format!("{location}.{key}[{index}]"), module));
            }
        }
        let bindings = self.bindings();
        for binding in &bindings {
            named.push((
                format!("{location}.{}", binding.key),
                binding.prefix.clone(),
            ));
        }
        let features = &architecture["features"];
        if let Some(root) = features["root"].as_str().filter(|root| !root.is_empty()) {
            named.push((format!("{location}.features.root"), root.to_owned()));
        }
        if let Some(pattern) = features["pattern"].as_str().filter(|text| !text.is_empty()) {
            let prefix = pattern.rsplit_once('.').map_or(pattern, |(head, _)| head);
            named.push((format!("{location}.features.pattern"), prefix.to_owned()));
        }
        for (index, pattern) in string_list(architecture.get("wiring"))
            .into_iter()
            .enumerate()
        {
            named.push((format!("{location}.wiring[{index}]"), pattern));
        }
        for (path, module) in named {
            self.check_scope(&path, &module);
        }
        self.overlapping_sets(&bindings);
        self.check_bindings(&bindings);
    }

    fn check_bindings(&mut self, bindings: &[Binding]) {
        let location = self.location.clone();
        let mut by_prefix: Vec<Binding> = Vec::new();
        for binding in bindings {
            let slot = by_prefix
                .iter()
                .position(|existing| existing.prefix == binding.prefix);
            let Some(slot) = slot else {
                by_prefix.push(binding.clone());
                continue;
            };
            let existing = &by_prefix[slot];
            let path = format!("{location}.{}", existing.key);
            if existing.layer == binding.layer {
                self.warnings.push(Issue::new(
                    path,
                    format!(
                        "\"{}\" is also declared at {location}.{}",
                        binding.prefix, binding.key
                    ),
                ));
            } else {
                self.errors.push(Issue::new(
                    path,
                    format!(
                        "\"{}\" also maps to layer \"{}\" at {location}.{}",
                        binding.prefix, binding.layer, binding.key
                    ),
                ));
            }
            by_prefix[slot] = binding.clone();
        }
        for binding in &by_prefix {
            let mut module = binding.prefix.rsplit_once('.').map_or("", |(head, _)| head);
            let mut parent = None;
            while !module.is_empty() {
                if let Some(found) = by_prefix.iter().find(|item| item.prefix == module) {
                    parent = Some(found);
                    break;
                }
                module = module.rsplit_once('.').map_or("", |(head, _)| head);
            }
            if let Some(parent) = parent
                && parent.layer == binding.layer
            {
                self.warnings.push(Issue::new(
                    format!("{location}.{}", binding.key),
                    format!(
                        "redundant layer assignment: \"{}\" inherits \"{}\" from \"{}\" ({location}.{})",
                        binding.prefix, parent.layer, parent.prefix, parent.key
                    ),
                ));
            }
        }
    }

    fn check_scope(&mut self, path: &str, module: &str) {
        let prefix = module
            .split('*')
            .next()
            .unwrap_or(module)
            .trim_end_matches('.');
        if self.roots.iter().any(|root| within(prefix, root)) {
            return;
        }
        if let Some(owner) = self.member_owners.get(first(prefix)).cloned() {
            self.error(
                path,
                format!(
                    "\"{module}\" belongs to the workspace member {owner}; declare it in that member's config"
                ),
            );
            return;
        }
        let place = if self.location == "architecture" {
            "project.root_packages"
        } else {
            "this member's packages"
        };
        let roots = self.roots.join(", ");
        self.error(
            path,
            format!("\"{module}\" is not inside {place} ({roots})"),
        );
    }

    fn overlapping_sets(&mut self, bindings: &[Binding]) {
        let architecture = self.architecture;
        let location = self.location.clone();
        let features_root = architecture["features"]["root"]
            .as_str()
            .filter(|root| !root.is_empty())
            .map(str::to_owned);
        let shared = string_list(architecture.get("shared"));
        let composition = string_list(architecture.get("composition_root"));
        for (key, modules) in [("shared", &shared), ("composition_root", &composition)] {
            for (index, module) in modules.iter().enumerate() {
                if let Some(root) = &features_root
                    && within(module, root)
                {
                    self.error(
                        format!("{location}.{key}[{index}]"),
                        format!("\"{module}\" is inside the features root \"{root}\""),
                    );
                }
            }
        }
        for binding in bindings {
            let module = &binding.prefix;
            let path = format!("{location}.{}", binding.key);
            if let Some(root) = &features_root
                && (within(module, root) || within(root, module))
            {
                self.error(
                    path.clone(),
                    format!(
                        "\"{module}\" overlaps the features root \"{root}\"; modules are for code outside the features"
                    ),
                );
            }
            for other in shared.iter().chain(&composition) {
                if within(module, other) {
                    self.error(
                        path.clone(),
                        format!(
                            "\"{module}\" is already covered by \"{other}\" (shared or composition_root)"
                        ),
                    );
                }
            }
        }
        for (index, module) in shared.iter().enumerate() {
            for other in &composition {
                if within(module, other) || within(other, module) {
                    self.error(
                        format!("{location}.shared[{index}]"),
                        format!("\"{module}\" overlaps composition_root \"{other}\""),
                    );
                }
            }
        }
    }
}

fn first(name: &str) -> &str {
    name.split('.').next().unwrap_or(name)
}

fn find_layer_cycle(layers: &Value) -> Option<Vec<String>> {
    let layers = layers.as_object()?;
    let mut names: Vec<&String> = layers.keys().collect();
    names.sort();
    let mut state: HashMap<String, u8> = HashMap::new();
    let mut stack: Vec<String> = Vec::new();
    for name in names {
        if !state.contains_key(name)
            && let Some(cycle) = visit_layer(name, layers, &mut state, &mut stack)
        {
            return Some(cycle);
        }
    }
    None
}

fn visit_layer(
    name: &str,
    layers: &serde_json::Map<String, Value>,
    state: &mut HashMap<String, u8>,
    stack: &mut Vec<String>,
) -> Option<Vec<String>> {
    state.insert(name.to_owned(), 1);
    stack.push(name.to_owned());
    for dependency in string_list(layers[name].get("may_depend_on")) {
        if !layers.contains_key(&dependency) || dependency == name {
            continue;
        }
        match state.get(&dependency) {
            Some(1) => {
                let start = stack.iter().position(|item| *item == dependency)?;
                let mut cycle = stack[start..].to_vec();
                cycle.push(dependency);
                return Some(cycle);
            }
            None => {
                if let Some(cycle) = visit_layer(&dependency, layers, state, stack) {
                    return Some(cycle);
                }
            }
            _ => {}
        }
    }
    stack.pop();
    state.insert(name.to_owned(), 2);
    None
}
