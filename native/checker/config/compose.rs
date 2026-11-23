//! Explicit local policy composition, as in `smelt.config.composition`.
use super::errors::{ConfigError, Issue, Segment, format_loc};
use super::models::POLICY_FIELDS;
use super::paths;
use super::schema::key_segment;
use super::workspace::member_raw_issues;
use super::yaml::{self, Yaml};
use std::collections::BTreeMap;
use std::path::{Component, Path, PathBuf};

const MAX_DEPTH: usize = 32;

type Pairs = Vec<(Yaml, Yaml)>;
type Origins = BTreeMap<String, String>;

#[derive(Default)]
pub struct Composition {
    pub raw: Pairs,
    pub files: Vec<PathBuf>,
    pub origins: Origins,
    pub rule_layers: Vec<Yaml>,
    pub rule_sources: Vec<String>,
}

impl Composition {
    /// Whether values can come from more than one file.
    pub fn composite(&self) -> bool {
        self.files.len() > 1
    }

    pub fn raw_value(&self) -> Yaml {
        Yaml::Map(self.raw.clone())
    }

    pub fn has_key(&self, key: &str) -> bool {
        self.raw
            .iter()
            .any(|(candidate, _)| candidate.as_str() == Some(key))
    }

    /// The file that declared the nearest key of `location`.
    pub fn source_for(&self, location: &str) -> Option<String> {
        source_for(&self.origins, location)
    }

    /// Whether the composed file sets `project.workspace: uv`.
    pub fn declares_workspace(&self) -> bool {
        self.raw_value()
            .get("project")
            .and_then(|project| project.get("workspace"))
            .and_then(Yaml::as_str)
            == Some("uv")
    }

    /// Name a member's key as written in its own file.
    pub fn member_relative(&self, location: &str) -> String {
        let raw = self.raw_value();
        let prefix = raw
            .get("members")
            .and_then(Yaml::as_map)
            .into_iter()
            .flatten()
            .filter_map(|(member, _)| member.as_str())
            .map(|member| format!("members.{member}."))
            .filter(|prefix| location.starts_with(prefix.as_str()))
            .max_by_key(String::len);
        match prefix {
            Some(prefix) => location[prefix.len()..].to_owned(),
            None => location.to_owned(),
        }
    }
}

pub fn source_for(origins: &Origins, location: &str) -> Option<String> {
    origins
        .iter()
        .filter(|(key, _)| {
            location == key.as_str()
                || location
                    .strip_prefix(key.as_str())
                    .is_some_and(|rest| rest.starts_with(['.', '[']))
        })
        .max_by_key(|(key, _)| key.len())
        .map(|(_, source)| source.clone())
}

/// Read one parent per file and merge raw values before applying defaults.
pub fn compose_config(path: &Path) -> Result<Composition, ConfigError> {
    let mut composed = Composition::default();
    visit(&paths::resolve(path), &mut Vec::new(), &mut composed)?;
    Ok(composed)
}

/// Place a member's own config under `members.<path>`.
pub fn add_member(
    composed: &mut Composition,
    member: &str,
    path: &Path,
) -> Result<(), ConfigError> {
    let source = paths::posix(path);
    let text = paths::read_text(path).map_err(|error| {
        ConfigError::new(
            vec![Issue::new(
                "",
                format!("cannot read {}: {error}", path.display()),
            )],
            Some(source.clone()),
        )
    })?;
    let raw = yaml::load(&text).map_err(|error| {
        ConfigError::new(
            vec![Issue::new("", format!("invalid YAML: {error}"))],
            Some(source.clone()),
        )
    })?;
    let raw = if matches!(raw, Yaml::Null) {
        Yaml::Map(Vec::new())
    } else {
        raw
    };
    let issues = member_raw_issues(&raw);
    if !issues.is_empty() {
        return Err(ConfigError::new(issues, Some(source)));
    }
    check_tree(&raw, &mut Vec::new(), &source)?;
    let incoming = vec![(Yaml::Str(member.to_owned()), raw)];
    let index = match composed
        .raw
        .iter()
        .position(|(key, _)| key.as_str() == Some("members"))
    {
        Some(index) => index,
        None => {
            composed
                .raw
                .push((Yaml::Str("members".to_owned()), Yaml::Map(Vec::new())));
            composed.raw.len() - 1
        }
    };
    if let Yaml::Map(target) = &mut composed.raw[index].1 {
        merge(
            target,
            &incoming,
            &[Segment::Key("members".to_owned())],
            &source,
            &mut composed.origins,
        );
    }
    composed.files.push(path.to_path_buf());
    Ok(())
}

fn visit(
    path: &Path,
    parents: &mut Vec<PathBuf>,
    composed: &mut Composition,
) -> Result<(), ConfigError> {
    let source = paths::posix(path);
    let referring = parents.last().map(|parent| paths::posix(parent));
    if parents.iter().any(|parent| paths::same_path(parent, path)) {
        let chain: Vec<String> = parents
            .iter()
            .map(|parent| paths::posix(parent))
            .chain([source])
            .collect();
        return Err(ConfigError::new(
            vec![Issue::new(
                "extends",
                format!("configuration inheritance cycle: {}", chain.join(" -> ")),
            )],
            referring,
        ));
    }
    if parents.len() >= MAX_DEPTH {
        return Err(ConfigError::new(
            vec![Issue::new(
                "extends",
                format!("configuration inheritance exceeds {MAX_DEPTH} files"),
            )],
            referring,
        ));
    }
    let text = paths::read_text(path).map_err(|error| {
        let location = if parents.is_empty() { "" } else { "extends" };
        ConfigError::new(
            vec![Issue::new(
                location,
                format!("cannot read {}: {error}", path.display()),
            )],
            Some(referring.clone().unwrap_or_else(|| source.clone())),
        )
    })?;
    let raw = yaml::load(&text).map_err(|error| {
        ConfigError::new(
            vec![Issue::new("", format!("invalid YAML: {error}"))],
            Some(source.clone()),
        )
    })?;
    let raw = match raw {
        Yaml::Null => Yaml::Map(Vec::new()),
        other => other,
    };
    let Yaml::Map(pairs) = &raw else {
        return Err(ConfigError::one(
            "",
            "the top level must be a mapping",
            &source,
        ));
    };
    check_tree(&raw, &mut Vec::new(), &source)?;
    if let Some(parent) = raw.get("extends") {
        let parent = check_parent(parent, &source)?;
        let next = paths::resolve(&path.parent().unwrap_or(path).join(parent));
        parents.push(path.to_path_buf());
        let outcome = visit(&next, parents, composed);
        parents.pop();
        outcome?;
    }
    let own: Pairs = pairs
        .iter()
        .filter(|(key, _)| key.as_str() != Some("extends"))
        .cloned()
        .collect();
    merge(&mut composed.raw, &own, &[], &source, &mut composed.origins);
    if let Some(rules @ Yaml::Map(entries)) = raw.get("rules")
        && !entries.is_empty()
    {
        composed.rule_layers.push(rules.clone());
        composed.rule_sources.push(source);
    }
    composed.files.push(path.to_path_buf());
    Ok(())
}

fn check_parent<'a>(parent: &'a Yaml, source: &str) -> Result<&'a str, ConfigError> {
    if let Some(text) = parent.as_str()
        && text.starts_with("smelt:")
    {
        return Err(ConfigError::one(
            "extends",
            "built-in presets were removed; list rules individually (smelt init --maintainability writes a starting list) or extend a local base file",
            source,
        ));
    }
    match parent.as_str() {
        Some(text) if !text.trim().is_empty() && !has_anchor(text) && !text.contains("://") => {
            Ok(text)
        }
        _ => Err(ConfigError::one(
            "extends",
            "must be one nonempty local relative file path",
            source,
        )),
    }
}

fn has_anchor(text: &str) -> bool {
    let path = Path::new(text);
    path.has_root() || matches!(path.components().next(), Some(Component::Prefix(_)))
}

fn check_tree(value: &Yaml, location: &mut Vec<Segment>, source: &str) -> Result<(), ConfigError> {
    match value {
        Yaml::Recursive => Err(ConfigError::one(
            &format_loc(location),
            "recursive YAML values are not supported",
            source,
        )),
        Yaml::Map(pairs) => {
            for (key, child) in pairs {
                location.push(key_segment(key));
                check_tree(child, location, source)?;
                location.pop();
            }
            Ok(())
        }
        Yaml::Seq(items) => {
            for (index, child) in items.iter().enumerate() {
                location.push(Segment::Index(index));
                check_tree(child, location, source)?;
                location.pop();
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

fn merge(
    target: &mut Pairs,
    incoming: &[(Yaml, Yaml)],
    prefix: &[Segment],
    source: &str,
    origins: &mut Origins,
) {
    if is_style_scope(prefix) {
        drop_replaced_reasons(target, incoming, prefix, origins);
    }
    for (key, value) in incoming {
        let mut location = prefix.to_vec();
        location.push(key_segment(key));
        let shown = format_loc(&location);
        let index = target
            .iter()
            .position(|(candidate, _)| candidate.same_key(key));
        let nested = match (value, index.map(|slot| &target[slot].1)) {
            (Yaml::Map(new), Some(Yaml::Map(old))) => !replaces_style_value(&location, new, old),
            _ => false,
        };
        if let (true, Some(slot), Yaml::Map(new)) = (nested, index, value) {
            if let Yaml::Map(previous) = &mut target[slot].1 {
                merge(previous, new, &location, source, origins);
            }
        } else {
            let (dotted, indexed) = (format!("{shown}."), format!("{shown}["));
            origins.retain(|old, _| {
                *old != shown && !old.starts_with(&dotted) && !old.starts_with(&indexed)
            });
            let stored = if let Yaml::Map(new) = value {
                let mut fresh = Vec::new();
                merge(&mut fresh, new, &location, source, origins);
                Yaml::Map(fresh)
            } else {
                value.clone()
            };
            match index {
                Some(slot) => target[slot].1 = stored,
                None => target.push((key.clone(), stored)),
            }
            origins.insert(shown, source.to_owned());
        }
        if let Yaml::Seq(items) = value {
            list_origins(items, &location, source, origins);
        }
    }
}

fn key_is(segment: &Segment, name: &str) -> bool {
    matches!(segment, Segment::Key(key) if key == name)
}

fn is_style_scope(prefix: &[Segment]) -> bool {
    match prefix {
        [style] => key_is(style, "style"),
        [style, overrides, _] => key_is(style, "style") && key_is(overrides, "overrides"),
        _ => false,
    }
}

/// Keep legacy reasons attached to the option value they originally explained.
fn drop_replaced_reasons(
    target: &mut Pairs,
    incoming: &[(Yaml, Yaml)],
    prefix: &[Segment],
    origins: &mut Origins,
) {
    let has = |pairs: &[(Yaml, Yaml)], name: &str| {
        pairs.iter().any(|(key, _)| key.as_str() == Some(name))
    };
    let declared: &[(Yaml, Yaml)] = match incoming
        .iter()
        .find(|(key, _)| key.as_str() == Some("reasons"))
    {
        None => &[],
        Some((_, Yaml::Map(entries))) => entries,
        Some(_) => return,
    };
    let Some((_, Yaml::Map(previous))) = target
        .iter_mut()
        .find(|(key, _)| key.as_str() == Some("reasons"))
    else {
        return;
    };
    previous.retain(|(key, _)| {
        let Some(name) = key.as_str() else {
            return true;
        };
        if !POLICY_FIELDS.contains(&name) || !has(incoming, name) || has(declared, name) {
            return true;
        }
        let mut location = prefix.to_vec();
        location.push(Segment::Key("reasons".to_owned()));
        location.push(Segment::Key(name.to_owned()));
        origins.remove(&format_loc(&location));
        false
    });
}

/// A reason belongs to the whole style option written by one file.
fn replaces_style_value(
    location: &[Segment],
    value: &[(Yaml, Yaml)],
    previous: &[(Yaml, Yaml)],
) -> bool {
    let name = match location {
        [style, name] if key_is(style, "style") => name,
        [style, overrides, _, name] if key_is(style, "style") && key_is(overrides, "overrides") => {
            name
        }
        _ => return false,
    };
    let Segment::Key(name) = name else {
        return false;
    };
    POLICY_FIELDS.contains(&name.as_str())
        && previous
            .iter()
            .chain(value)
            .any(|(key, _)| matches!(key.as_str(), Some("value" | "reason")))
}

fn list_origins(values: &[Yaml], prefix: &[Segment], source: &str, origins: &mut Origins) {
    for (index, value) in values.iter().enumerate() {
        let mut location = prefix.to_vec();
        location.push(Segment::Index(index));
        origins.insert(format_loc(&location), source.to_owned());
        match value {
            Yaml::Map(pairs) => merge(&mut Vec::new(), pairs, &location, source, origins),
            Yaml::Seq(items) => list_origins(items, &location, source, origins),
            _ => {}
        }
    }
}
