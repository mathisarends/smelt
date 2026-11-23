//! A small declarative validator with the error locations of the Python models.
use super::errors::{Issue, Segment, did_you_mean, format_loc, upgrade_guide_url};
use super::yaml::Yaml;
use regex::Regex;
use serde_json::{Map, Number, Value};
use std::sync::LazyLock;

pub type Out = Map<String, Value>;
pub type ModelRef = &'static LazyLock<ModelDef>;
pub type Check = fn(&mut Value) -> Result<(), String>;

const LIMIT_OPTIONS: [&str; 7] = [
    "max_module_imports",
    "max_positional_parameters",
    "max_comprehension_loops",
    "max_constructor_decisions",
    "max_private_definitions",
    "max_patches_per_test",
    "max_uncovered_facade_callers",
];
const RENAMED_LIMITS: [(&str, &str); 7] = [
    ("max_module_imports", "imports/excess-module-imports"),
    (
        "max_positional_parameters",
        "complexity/excess-positional-parameters",
    ),
    (
        "max_comprehension_loops",
        "complexity/excess-comprehension-loops",
    ),
    (
        "max_constructor_decisions",
        "complexity/excess-constructor-decisions",
    ),
    (
        "max_private_definitions",
        "visibility/excess-private-definitions",
    ),
    ("max_patches_per_test", "tests/excess-patches"),
    (
        "max_uncovered_facade_callers",
        "meta/uncovered-package-facade",
    ),
];
const REMOVED_SWITCHES: [(&str, &str); 12] = [
    ("member_visibility", "visibility/internal-public-member"),
    ("static_methods", "layout/misplaced-static-helper"),
    ("instance_assignments", "idioms/bundled-instance-assignment"),
    ("forbid_private_imports", "tests/private-access"),
    ("forbid_string_patch_targets", "tests/string-patch-target"),
    ("require_assertions", "tests/missing-outcome-check"),
    ("require_non_vacuous_assertions", "tests/vacuous-assertion"),
    ("review_test_deadlines", "tests/unbounded-wait"),
    ("review_case_loops", "tests/independent-case-loop"),
    (
        "review_async_mock_assertions",
        "tests/missing-await-assertion",
    ),
    (
        "review_test_only_public_functions",
        "visibility/test-only-public-function",
    ),
    (
        "review_test_only_public_attributes",
        "visibility/test-only-public-attribute",
    ),
];

static INTEGER_TEXT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^[+-]?[0-9]+(?:_[0-9]+)*(?:\.0*)?$").expect("the integer pattern is valid")
});

pub enum Ty {
    Str,
    NonEmptyStr,
    TrimmedReason,
    Pattern(&'static str, &'static LazyLock<Regex>),
    Int(Option<i64>),
    Bool,
    Lit(&'static [&'static str]),
    Version,
    List(Box<Ty>),
    NonEmptyList(Box<Ty>),
    Dict(Box<Ty>),
    Opt(Box<Ty>),
    Model(ModelRef),
    /// `str | Model`, reported at the entry without the union branch.
    StrOr(ModelRef),
    /// `RuleSeverity | RuleOverride`.
    Rule(ModelRef),
    Checked(Box<Ty>, Check),
}

pub fn list(item: Ty) -> Ty {
    Ty::List(Box::new(item))
}

pub fn dict(item: Ty) -> Ty {
    Ty::Dict(Box::new(item))
}

pub fn opt(item: Ty) -> Ty {
    Ty::Opt(Box::new(item))
}

pub fn checked(item: Ty, check: Check) -> Ty {
    Ty::Checked(Box::new(item), check)
}

pub enum Default {
    Required,
    Value(Value),
    Model,
}

pub struct Field {
    pub key: &'static str,
    pub ty: Ty,
    pub default: Default,
}

pub fn required(key: &'static str, ty: Ty) -> Field {
    Field {
        key,
        ty,
        default: Default::Required,
    }
}

pub fn with_default(key: &'static str, ty: Ty, default: Value) -> Field {
    Field {
        key,
        ty,
        default: Default::Value(default),
    }
}

pub fn optional(key: &'static str, ty: Ty) -> Field {
    with_default(key, opt(ty), Value::Null)
}

pub fn nested(key: &'static str, model: ModelRef) -> Field {
    Field {
        key,
        ty: Ty::Model(model),
        default: Default::Model,
    }
}

type BeforeCheck = fn(&Yaml) -> Result<Yaml, String>;
type AfterCheck = fn(&mut Out) -> Result<(), String>;

pub struct ModelDef {
    pub name: &'static str,
    pub fields: Vec<Field>,
    pub before: Option<BeforeCheck>,
    pub after: Option<AfterCheck>,
    /// Keep the keys the input set, like pydantic's `model_fields_set`.
    pub tracks_set: bool,
}

impl ModelDef {
    pub fn new(name: &'static str, fields: Vec<Field>) -> Self {
        Self {
            name,
            fields,
            before: None,
            after: None,
            tracks_set: false,
        }
    }

    pub fn before(mut self, check: fn(&Yaml) -> Result<Yaml, String>) -> Self {
        self.before = Some(check);
        self
    }

    pub fn after(mut self, check: fn(&mut Out) -> Result<(), String>) -> Self {
        self.after = Some(check);
        self
    }

    pub fn tracked(mut self) -> Self {
        self.tracks_set = true;
        self
    }
}

#[derive(Default)]
pub struct Validator {
    pub issues: Vec<Issue>,
    location: Vec<Segment>,
    unions: usize,
    /// The next model was reached through an optional field, which has no suggestions.
    optional_model: bool,
}

impl Validator {
    /// A validator whose locations start below `key`.
    pub fn at_key(key: &str) -> Self {
        Self {
            location: vec![Segment::Key(key.to_owned())],
            ..Self::default()
        }
    }

    fn error(&mut self, message: impl Into<String>) {
        self.issues
            .push(Issue::new(format_loc(&self.location), message));
    }

    fn within<R>(&mut self, segment: Segment, run: impl FnOnce(&mut Self) -> R) -> R {
        self.location.push(segment);
        let result = run(self);
        self.location.pop();
        result
    }

    pub fn model(&mut self, def: &ModelDef, input: &Yaml) -> Option<Value> {
        let suggest = !std::mem::take(&mut self.optional_model) && self.unions == 0;
        let rewritten;
        let input = match def.before {
            Some(before) => match before(input) {
                Ok(value) => {
                    rewritten = value;
                    &rewritten
                }
                Err(message) => {
                    self.error(message);
                    return None;
                }
            },
            None => input,
        };
        let Some(pairs) = input.as_map() else {
            self.error(format!(
                "Input should be a valid dictionary or instance of {}",
                def.name
            ));
            return None;
        };
        let mut out = Out::new();
        let mut valid = true;
        for field in &def.fields {
            let found = pairs
                .iter()
                .find(|(key, _)| key.as_str() == Some(field.key))
                .map(|(_, value)| value);
            let segment = Segment::Key(field.key.to_owned());
            let value = match (found, &field.default) {
                (Some(value), _) => self.within(segment, |this| this.check(&field.ty, value)),
                (None, Default::Value(value)) => Some(value.clone()),
                (None, Default::Model) => self.within(segment, |this| {
                    this.check(&field.ty, &Yaml::Map(Vec::new()))
                }),
                (None, Default::Required) => {
                    self.within(segment, |this| this.error("required key is missing"));
                    None
                }
            };
            match value {
                Some(value) => {
                    out.insert(field.key.to_owned(), value);
                }
                None => valid = false,
            }
        }
        for (key, _) in pairs {
            let Some(name) = key.as_str() else {
                self.within(key_segment(key), |this| {
                    this.error("Keys should be strings")
                });
                valid = false;
                continue;
            };
            if def.fields.iter().any(|field| field.key == name) {
                continue;
            }
            valid = false;
            let known: Vec<&str> = if suggest {
                def.fields.iter().map(|field| field.key).collect()
            } else {
                Vec::new()
            };
            self.within(Segment::Key(name.to_owned()), |this| {
                let message = unknown_key_message(name, &known, &this.location);
                this.error(message);
            });
        }
        if !valid {
            return None;
        }
        if def.tracks_set {
            let set: Vec<Value> = pairs
                .iter()
                .filter_map(|(key, _)| key.as_str())
                .map(|key| Value::String(key.to_owned()))
                .collect();
            out.insert("__set__".to_owned(), Value::Array(set));
        }
        if let Some(after) = def.after
            && let Err(message) = after(&mut out)
        {
            self.error(message);
            return None;
        }
        Some(Value::Object(out))
    }

    pub fn check(&mut self, ty: &Ty, input: &Yaml) -> Option<Value> {
        match ty {
            Ty::Str => self.string(input).map(Value::String),
            Ty::NonEmptyStr => {
                let value = self.string(input)?;
                if value.is_empty() {
                    self.error("String should have at least 1 character");
                    return None;
                }
                Some(Value::String(value))
            }
            Ty::TrimmedReason => {
                let value = self.string(input)?;
                let trimmed = value.trim();
                if trimmed.is_empty() {
                    self.error("String should have at least 1 character");
                    return None;
                }
                Some(Value::String(trimmed.to_owned()))
            }
            Ty::Pattern(pattern, regex) => {
                let value = self.string(input)?;
                if !regex.is_match(&value) {
                    self.error(format!("String should match pattern '{pattern}'"));
                    return None;
                }
                Some(Value::String(value))
            }
            Ty::Int(minimum) => self.integer(input, *minimum),
            Ty::Bool => self.boolean(input),
            Ty::Lit(options) => {
                if let Some(value) = input.as_str()
                    && options.contains(&value)
                {
                    return Some(Value::String(value.to_owned()));
                }
                self.error(format!(
                    "{}, got {}",
                    literal_message(options),
                    input.repr()
                ));
                None
            }
            Ty::Version => {
                if matches!(input, Yaml::Int(2)) || matches!(input, Yaml::Float(v) if *v == 2.0) {
                    return Some(Value::from(2));
                }
                self.error(format!("Input should be 2, got {}", input.repr()));
                None
            }
            Ty::List(item) => self.sequence(item, input),
            Ty::NonEmptyList(item) => {
                let value = self.sequence(item, input)?;
                if value.as_array().is_some_and(Vec::is_empty) {
                    self.error("List should have at least 1 item after validation, not 0");
                    return None;
                }
                Some(value)
            }
            Ty::Dict(item) => self.mapping(item, input),
            Ty::Opt(item) => {
                if matches!(input, Yaml::Null) {
                    return Some(Value::Null);
                }
                self.optional_model = matches!(**item, Ty::Model(_));
                self.check(item, input)
            }
            Ty::Model(model) => self.model(model, input),
            Ty::StrOr(model) => match input {
                Yaml::Str(value) => Some(Value::String(value.clone())),
                Yaml::Map(_) => self.in_union(|this| this.model(model, input)),
                _ => {
                    self.error("Input should be a valid string");
                    self.error(format!(
                        "Input should be a valid dictionary or instance of {}",
                        model.name
                    ));
                    None
                }
            },
            Ty::Rule(model) => match input {
                Yaml::Map(_) => self.in_union(|this| this.model(model, input)),
                _ => self.check(&Ty::Lit(&["error", "warning", "hint", "on", "off"]), input),
            },
            Ty::Checked(item, check) => {
                let mut value = self.check(item, input)?;
                if let Err(message) = check(&mut value) {
                    self.error(message);
                    return None;
                }
                Some(value)
            }
        }
    }

    fn in_union<R>(&mut self, run: impl FnOnce(&mut Self) -> R) -> R {
        self.unions += 1;
        let result = run(self);
        self.unions -= 1;
        result
    }

    fn string(&mut self, input: &Yaml) -> Option<String> {
        if let Some(value) = input.as_str() {
            return Some(value.to_owned());
        }
        self.error("Input should be a valid string");
        None
    }

    fn integer(&mut self, input: &Yaml, minimum: Option<i64>) -> Option<Value> {
        let parsed = match input {
            Yaml::Bool(value) => Some(i128::from(*value)),
            Yaml::Int(value) => Some(*value),
            Yaml::Float(value) if !value.is_finite() => {
                self.error("Input should be a finite number");
                return None;
            }
            Yaml::Float(value) => {
                if value.fract() != 0.0 {
                    self.error(
                        "Input should be a valid integer, got a number with a fractional part",
                    );
                    return None;
                }
                Some(*value as i128)
            }
            Yaml::Str(text) => {
                let trimmed = text.trim();
                if !INTEGER_TEXT.is_match(trimmed) {
                    self.error(
                        "Input should be a valid integer, unable to parse string as an integer",
                    );
                    return None;
                }
                let digits = trimmed
                    .split('.')
                    .next()
                    .unwrap_or(trimmed)
                    .replace('_', "");
                digits.parse().ok()
            }
            _ => None,
        };
        let Some(parsed) = parsed else {
            self.error("Input should be a valid integer");
            return None;
        };
        if let Some(minimum) = minimum
            && parsed < i128::from(minimum)
        {
            self.error(format!(
                "Input should be greater than or equal to {minimum}"
            ));
            return None;
        }
        Some(match i64::try_from(parsed) {
            Ok(value) => Value::from(value),
            Err(_) => Number::from_f64(parsed as f64).map_or(Value::Null, Value::Number),
        })
    }

    fn boolean(&mut self, input: &Yaml) -> Option<Value> {
        let parsed = match input {
            Yaml::Bool(value) => Some(*value),
            Yaml::Int(0) => Some(false),
            Yaml::Int(1) => Some(true),
            Yaml::Float(value) if *value == 0.0 => Some(false),
            Yaml::Float(value) if *value == 1.0 => Some(true),
            Yaml::Str(text) => match text.to_lowercase().as_str() {
                "0" | "off" | "f" | "n" | "no" | "false" => Some(false),
                "1" | "on" | "t" | "y" | "yes" | "true" => Some(true),
                _ => None,
            },
            Yaml::Int(value) if i64::try_from(*value).is_ok() => None,
            Yaml::Float(value)
                if value.fract() == 0.0 && value.abs() < 9_223_372_036_854_775_808.0 =>
            {
                None
            }
            _ => {
                self.error("Input should be a valid boolean");
                return None;
            }
        };
        if parsed.is_none() {
            self.error("Input should be a valid boolean, unable to interpret input");
        }
        parsed.map(Value::Bool)
    }

    fn sequence(&mut self, item: &Ty, input: &Yaml) -> Option<Value> {
        let Yaml::Seq(items) = input else {
            self.error("Input should be a valid list");
            return None;
        };
        let mut values = Vec::with_capacity(items.len());
        let mut valid = true;
        for (index, entry) in items.iter().enumerate() {
            match self.within(Segment::Index(index), |this| this.check(item, entry)) {
                Some(value) => values.push(value),
                None => valid = false,
            }
        }
        valid.then_some(Value::Array(values))
    }

    fn mapping(&mut self, item: &Ty, input: &Yaml) -> Option<Value> {
        let Yaml::Map(pairs) = input else {
            self.error("Input should be a valid dictionary");
            return None;
        };
        let mut values = Out::new();
        let mut valid = true;
        for (key, entry) in pairs {
            let Some(name) = key.as_str() else {
                self.within(key_segment(key), |this| {
                    this.within(Segment::Key("[key]".to_owned()), |inner| {
                        inner.error("Input should be a valid string");
                    });
                    this.check(item, entry);
                });
                valid = false;
                continue;
            };
            let segment = Segment::Key(name.to_owned());
            match self.within(segment, |this| this.check(item, entry)) {
                Some(value) => {
                    values.insert(name.to_owned(), value);
                }
                None => valid = false,
            }
        }
        valid.then_some(Value::Object(values))
    }
}

/// A mapping key as one location step; Python renders integer keys like indexes.
pub fn key_segment(key: &Yaml) -> Segment {
    match key {
        Yaml::Str(value) => Segment::Key(value.clone()),
        Yaml::Int(value) => usize::try_from(*value)
            .map_or_else(|_| Segment::Bracket(value.to_string()), Segment::Index),
        Yaml::Bool(_) => Segment::Bracket(key.repr()),
        other => Segment::Key(other.repr()),
    }
}

/// The strings of a JSON array; anything else has none.
pub fn string_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn literal_message(options: &[&str]) -> String {
    let quoted: Vec<String> = options.iter().map(|option| format!("'{option}'")).collect();
    match quoted.split_last() {
        Some((last, [])) => format!("Input should be {last}"),
        Some((last, rest)) => format!("Input should be {} or {last}", rest.join(", ")),
        None => "Input should be a valid value".to_owned(),
    }
}

fn retired_message(path: &[String]) -> Option<String> {
    let joined: Vec<&str> = path.iter().map(String::as_str).collect();
    let fixed = match joined.as_slice() {
        ["architecture", "modules"] => Some(
            "architecture.modules was removed; run smelt config migrate to move the assignments to architecture.layers.<layer>.modules",
        ),
        ["architecture", "imports"] => {
            Some("architecture.imports was renamed to architecture.import_graph")
        }
        ["style", "contracts"] => {
            Some("use style.overrides for conventions and architecture.packages for package APIs")
        }
        ["style", "canonical_exports"] => {
            Some("canonical_exports moved to architecture.canonical_exports")
        }
        ["project", "coverage"] => Some(
            "project.coverage was replaced by the rule; set rules.architecture/uncovered-source to error (smelt config migrate does this)",
        ),
        ["structure"] => Some("structure was replaced by style and architecture.packages"),
        ["bundle_imports_at"] => Some("bundle_imports_at was renamed to min_imports_to_bundle"),
        ["export_at"] => Some("export_at was renamed to min_importers_to_export"),
        ["module_import_exempt"] => {
            Some("module_import_exempt was renamed to module_import_exemptions")
        }
        ["unmirrored"] => Some("unmirrored was renamed to mirror_exclude"),
        ["forbid_mocks_in"] => Some("forbid_mocks_in was renamed to forbidden_mock_paths"),
        _ => None,
    };
    if let Some(message) = fixed {
        return Some(message.to_owned());
    }
    if let [option] = joined.as_slice()
        && LIMIT_OPTIONS.contains(option)
    {
        let name = RENAMED_LIMITS
            .iter()
            .find(|(limit, _)| limit == option)
            .map_or("", |(_, name)| *name);
        return Some(format!(
            "{option} moved into the rule entry; write rules: {{{name}: {{severity: ..., max: ...}}}} in the same scope (smelt config migrate does this)"
        ));
    }
    None
}

fn unknown_key_message(key: &str, known: &[&str], location: &[Segment]) -> String {
    let tail: Vec<String> = location[location.len().saturating_sub(2)..]
        .iter()
        .map(|segment| match segment {
            Segment::Key(name) | Segment::Bracket(name) => name.clone(),
            Segment::Index(index) => index.to_string(),
        })
        .collect();
    if let Some(retired) =
        retired_message(&tail).or_else(|| retired_message(std::slice::from_ref(&key.to_owned())))
    {
        return format!("{retired}; see {}", upgrade_guide_url());
    }
    let mut message = format!("unknown key \"{key}\"{}", did_you_mean(key, known));
    if let Some((_, rule)) = REMOVED_SWITCHES.iter().find(|(name, _)| *name == key) {
        return format!(
            "{message}; this switch was removed: configure rules.{rule} with error, warning, hint or off instead"
        );
    }
    let in_ordering = location
        .iter()
        .any(|segment| matches!(segment, Segment::Key(name) if name == "ordering"));
    if key == "enabled" && in_ordering {
        message.push_str("; enable or disable rules.layout/definition-order instead");
    } else if key == "preset" {
        message.push_str("; list rules individually or extend a local base file");
    }
    message
}
