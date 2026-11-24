//! The configuration models and their validators, as in `smelt.config.models`.
use super::schema::{
    Default as FieldDefault, Field, ModelDef, Out, Ty, checked, dict, list, nested, opt, optional,
    required, with_default,
};
use super::yaml::{Yaml, str_repr};
use regex::Regex;
use serde_json::{Value, json};
use std::sync::LazyLock;

pub const ALL_RULES: &str = "ALL";
pub const UNCOVERED_SOURCE: [&str; 2] = ["ARC013", "architecture/uncovered-source"];
pub const POLICY_FIELDS: [&str; 17] = [
    "imports",
    "reexports",
    "module_import_exemptions",
    "module_docstrings",
    "future_annotations",
    "private_modules",
    "public_attribute_boundary",
    "private_attribute_boundary",
    "ordering",
    "type_dispatch",
    "navigation",
    "call_argument_kinds",
    "str_enum_values",
    "dataclass_options",
    "bases",
    "third_party_reexports",
    "errors",
];
pub const RULE_LIMITS: [&str; 5] = [
    "max_module_imports",
    "max_positional_parameters",
    "max_comprehension_loops",
    "max_constructor_decisions",
    "max_private_definitions",
];
const LIMIT_RULES: [(&str, [&str; 2]); 5] = [
    (
        "max_module_imports",
        ["IMP002", "imports/excess-module-imports"],
    ),
    (
        "max_positional_parameters",
        ["CPX003", "complexity/excess-positional-parameters"],
    ),
    (
        "max_comprehension_loops",
        ["CPX006", "complexity/excess-comprehension-loops"],
    ),
    (
        "max_constructor_decisions",
        ["CPX007", "complexity/excess-constructor-decisions"],
    ),
    (
        "max_private_definitions",
        ["VIS007", "visibility/excess-private-definitions"],
    ),
];
pub const MODULE_ORDER: [&str; 8] = [
    "docstring",
    "future-imports",
    "imports",
    "dunder-assignments",
    "type-vars",
    "public",
    "private",
    "main-guard",
];
pub const CLASS_ORDER: [&str; 5] = ["class-attributes", "init", "dunders", "public", "private"];
const BUILTIN_EXCEPTIONS: [&str; 71] = [
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "BaseExceptionGroup",
    "BlockingIOError",
    "BrokenPipeError",
    "BufferError",
    "BytesWarning",
    "ChildProcessError",
    "ConnectionAbortedError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "DeprecationWarning",
    "EOFError",
    "EncodingWarning",
    "EnvironmentError",
    "Exception",
    "ExceptionGroup",
    "FileExistsError",
    "FileNotFoundError",
    "FloatingPointError",
    "FutureWarning",
    "GeneratorExit",
    "IOError",
    "ImportError",
    "ImportWarning",
    "IndentationError",
    "IndexError",
    "InterruptedError",
    "IsADirectoryError",
    "KeyError",
    "KeyboardInterrupt",
    "LookupError",
    "MemoryError",
    "ModuleNotFoundError",
    "NameError",
    "NotADirectoryError",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "PendingDeprecationWarning",
    "PermissionError",
    "ProcessLookupError",
    "PythonFinalizationError",
    "RecursionError",
    "ReferenceError",
    "ResourceWarning",
    "RuntimeError",
    "RuntimeWarning",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "SyntaxWarning",
    "SystemError",
    "SystemExit",
    "TabError",
    "TimeoutError",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeError",
    "UnicodeTranslateError",
    "UnicodeWarning",
    "UserWarning",
    "ValueError",
    "Warning",
    "ZeroDivisionError",
    "_IncompleteInputError",
];

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).expect("the configuration pattern is valid")
}

static DOTTED: LazyLock<Regex> =
    LazyLock::new(|| regex(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"));
static DOTTED_LINE: LazyLock<Regex> =
    LazyLock::new(|| regex(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\n?$"));
static RULE_CODE: LazyLock<Regex> = LazyLock::new(|| regex(r"^[A-Z]+[0-9]+\n?$"));
static RULE_NAME: LazyLock<Regex> =
    LazyLock::new(|| regex(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*(/([a-z][a-z0-9]*(-[a-z0-9]+)*)?)?\n?$"));
static LAYER_PAIR: LazyLock<Regex> =
    LazyLock::new(|| regex(r"^\s*([A-Za-z_][\w.]*)\s*->\s*([A-Za-z_][\w.]*)\s*$"));
static FEATURE_LAYER: LazyLock<Regex> =
    LazyLock::new(|| regex(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$"));
static QUALIFIED: LazyLock<Regex> = LazyLock::new(|| regex(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$"));
static DRIVE: LazyLock<Regex> = LazyLock::new(|| regex(r"^[A-Za-z]:"));
static PYTHON_VERSION: LazyLock<Regex> = LazyLock::new(|| regex(r"^3\.[0-9]+$"));
static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| regex(r"\{[^}]*\}"));

fn strings(value: &Value) -> Vec<&str> {
    value
        .as_array()
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default()
}

fn unique(values: Vec<String>) -> Value {
    let mut seen: Vec<String> = Vec::with_capacity(values.len());
    for value in values {
        if !seen.contains(&value) {
            seen.push(value);
        }
    }
    json!(seen)
}

fn check_dotted<'a>(values: impl IntoIterator<Item = &'a str>) -> Result<(), String> {
    for value in values {
        if !DOTTED_LINE.is_match(value) {
            return Err(format!("\"{value}\" is not a dotted module name"));
        }
    }
    Ok(())
}

fn dotted_list(value: &mut Value) -> Result<(), String> {
    check_dotted(strings(value))
}

fn is_pattern_segment(segment: &str) -> bool {
    segment == "*" || DOTTED.is_match(segment)
}

/// `posixpath.normpath`.
pub fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_owned();
    }
    let slashes = if path.starts_with("//") && !path.starts_with("///") {
        2
    } else {
        usize::from(path.starts_with('/'))
    };
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part != ".." || (slashes == 0 && parts.is_empty()) || parts.last() == Some(&"..") {
            parts.push(part);
        } else {
            parts.pop();
        }
    }
    let joined = format!("{}{}", "/".repeat(slashes), parts.join("/"));
    if joined.is_empty() {
        ".".to_owned()
    } else {
        joined
    }
}

/// `posixpath.join` for two paths.
pub fn join(base: &str, entry: &str) -> String {
    if entry.starts_with('/') || base.is_empty() {
        entry.to_owned()
    } else if base.ends_with('/') {
        format!("{base}{entry}")
    } else {
        format!("{base}/{entry}")
    }
}

fn relative_glob(raw: &str) -> Option<String> {
    let pattern = raw.trim().replace('\\', "/");
    if pattern.is_empty()
        || pattern.starts_with('/')
        || DRIVE.is_match(&pattern)
        || pattern.split('/').any(|part| part == "..")
    {
        return None;
    }
    Some(normpath(&pattern))
}

fn member_pattern(raw: &str) -> Result<String, String> {
    relative_glob(raw).ok_or_else(|| {
        "member globs must be nonempty workspace-relative paths without '..'".to_owned()
    })
}

fn normalize_roots(value: &mut Value) -> Result<(), String> {
    let roots = strings(value)
        .into_iter()
        .map(|root| normpath(&root.replace('\\', "/")))
        .collect();
    *value = unique(roots);
    Ok(())
}

fn root_packages(value: &mut Value) -> Result<(), String> {
    check_dotted(strings(value))?;
    for name in strings(value) {
        if name.contains('.') {
            return Err(format!("\"{name}\" must be a top-level package name"));
        }
    }
    Ok(())
}

fn trimmed_required(value: &mut Value) -> Result<(), String> {
    let text = value.as_str().unwrap_or_default().trim().to_owned();
    if text.is_empty() {
        return Err("must not be empty".to_owned());
    }
    *value = Value::String(text);
    Ok(())
}

fn override_reason(value: &mut Value) -> Result<(), String> {
    if let Some(text) = value.as_str() {
        let trimmed = text.trim().to_owned();
        if trimmed.is_empty() {
            return Err("reason must not be blank".to_owned());
        }
        *value = Value::String(trimmed);
    }
    Ok(())
}

fn rule_override_after(out: &mut Out) -> Result<(), String> {
    let missing = |key: &str| out.get(key).is_none_or(Value::is_null);
    if missing("max") && missing("severity") {
        return Err("a rule entry sets severity, max or both".to_owned());
    }
    if missing("max") && missing("reason") {
        return Err(
            "reason is required when the entry sets only a severity; write the severity directly otherwise"
                .to_owned(),
        );
    }
    Ok(())
}

pub static RULE_OVERRIDE: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "RuleOverride",
        vec![
            optional(
                "severity",
                Ty::Lit(&["error", "warning", "hint", "on", "off"]),
            ),
            optional("max", Ty::Int(Some(0))),
            with_default(
                "reason",
                checked(opt(Ty::NonEmptyStr), override_reason),
                Value::Null,
            ),
        ],
    )
    .after(rule_override_after)
});

static SOURCE_EXCLUSION: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "SourceExclusion",
        vec![
            required("path", checked(Ty::Str, trimmed_required)),
            required("reason", checked(Ty::Str, trimmed_required)),
        ],
    )
});

static PROJECT: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "ProjectConfig",
        vec![
            with_default(
                "root_packages",
                checked(list(Ty::Str), root_packages),
                json!([]),
            ),
            with_default(
                "source_roots",
                checked(list(Ty::Str), normalize_roots),
                json!(["."]),
            ),
            with_default(
                "test_roots",
                checked(list(Ty::Str), normalize_roots),
                json!(["tests"]),
            ),
            with_default("exclude", list(Ty::StrOr(&SOURCE_EXCLUSION)), json!([])),
            optional("workspace", Ty::Lit(&["uv"])),
            optional(
                "python_version",
                Ty::Pattern(r"^3\.[0-9]+$", &PYTHON_VERSION),
            ),
        ],
    )
    .tracked()
});

fn features_after(out: &mut Out) -> Result<(), String> {
    let root = out.get("root").and_then(Value::as_str);
    let pattern = out.get("pattern").and_then(Value::as_str);
    if root.is_none() == pattern.is_none() {
        return Err("set exactly one of `root` or `pattern`".to_owned());
    }
    if let Some(root) = root {
        check_dotted([root])?;
    }
    if let Some(pattern) = pattern {
        let segments: Vec<&str> = pattern.split('.').collect();
        let holes = segments.iter().filter(|part| **part == "{feature}").count();
        if holes != 1 || segments.last() != Some(&"{feature}") {
            return Err(format!(
                "pattern \"{pattern}\" must end with a \"{{feature}}\" segment, e.g. \"gateway.{{feature}}\""
            ));
        }
        if segments.len() < 2 {
            return Err("pattern needs a package before {feature}".to_owned());
        }
        check_dotted(segments[..segments.len() - 1].iter().copied())?;
    }
    Ok(())
}

static FEATURES: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "FeaturesConfig",
        vec![optional("root", Ty::Str), optional("pattern", Ty::Str)],
    )
    .after(features_after)
});

fn third_party_before(input: &Yaml) -> Result<Yaml, String> {
    match input.as_str() {
        Some(value @ ("allow" | "deny")) => Ok(Yaml::Map(vec![(
            Yaml::Str("default".to_owned()),
            Yaml::Str(value.to_owned()),
        )])),
        _ => Ok(input.clone()),
    }
}

fn third_party_after(out: &mut Out) -> Result<(), String> {
    let default = out.get("default").and_then(Value::as_str);
    let has = |key: &str| {
        out.get(key)
            .and_then(Value::as_array)
            .is_some_and(|v| !v.is_empty())
    };
    if default == Some("allow") && has("allow") {
        return Err("`allow` is only valid with `default: deny`".to_owned());
    }
    if default == Some("deny") && has("deny") {
        return Err("`deny` is only valid with `default: allow`".to_owned());
    }
    let allow = out.get("allow").map(strings).unwrap_or_default();
    let deny = out.get("deny").map(strings).unwrap_or_default();
    check_dotted(allow.into_iter().chain(deny))
}

const POLICY: &[&str] = &["allow", "deny"];

static THIRD_PARTY: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "ThirdPartyPolicy",
        vec![
            with_default("default", Ty::Lit(POLICY), json!("allow")),
            with_default("allow", list(Ty::Str), json!([])),
            with_default("deny", list(Ty::Str), json!([])),
        ],
    )
    .before(third_party_before)
    .after(third_party_after)
});

fn optional_dotted(value: &mut Value) -> Result<(), String> {
    if let Some(text) = value.as_str() {
        check_dotted([text])?;
    }
    Ok(())
}

fn layer_after(out: &mut Out) -> Result<(), String> {
    let no_modules = out
        .get("modules")
        .and_then(Value::as_array)
        .is_none_or(Vec::is_empty);
    if out.get("path").is_none_or(Value::is_null) && no_modules {
        return Err("a layer requires path or modules".to_owned());
    }
    Ok(())
}

static LAYER: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "LayerConfig",
        vec![
            with_default("path", checked(opt(Ty::Str), optional_dotted), Value::Null),
            with_default("modules", checked(list(Ty::Str), dotted_list), json!([])),
            with_default("may_depend_on", list(Ty::Str), json!([])),
            nested("third_party", &THIRD_PARTY),
        ],
    )
    .after(layer_after)
});

fn allowance_after(out: &mut Out) -> Result<(), String> {
    for key in ["from", "to"] {
        let value = out.get(key).and_then(Value::as_str).unwrap_or_default();
        if !FEATURE_LAYER.is_match(value) {
            return Err(format!("\"{value}\" must look like \"feature.layer\""));
        }
    }
    Ok(())
}

static ALLOWANCE: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "CrossFeatureAllowance",
        vec![required("from", Ty::Str), required("to", Ty::Str)],
    )
    .after(allowance_after)
});

fn pairs_well_formed(value: &mut Value) -> Result<(), String> {
    for text in value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        if !LAYER_PAIR.is_match(text) {
            return Err(format!("\"{text}\" must look like \"layer -> layer\""));
        }
    }
    Ok(())
}

static CROSS_FEATURE: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "CrossFeatureConfig",
        vec![
            with_default("default", Ty::Lit(POLICY), json!("deny")),
            with_default(
                "allow",
                checked(list(Ty::StrOr(&ALLOWANCE)), pairs_well_formed),
                json!([]),
            ),
        ],
    )
});

const INCLUDE_IGNORE: &[&str] = &["include", "ignore"];

static IMPORTS: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "ImportsConfig",
        vec![
            with_default("type_checking", Ty::Lit(INCLUDE_IGNORE), json!("include")),
            with_default("transitive", Ty::Bool, json!(false)),
            with_default(
                "cycles",
                list(Ty::Lit(&["features", "layers", "siblings", "modules"])),
                json!(["features", "layers", "siblings"]),
            ),
            with_default(
                "type_checking_cycles",
                Ty::Lit(INCLUDE_IGNORE),
                json!("include"),
            ),
        ],
    )
});

fn member_globs(value: &mut Value) -> Result<(), String> {
    let mut patterns = Vec::new();
    for entry in strings(value) {
        patterns.push(member_pattern(entry)?);
    }
    *value = unique(patterns);
    Ok(())
}

static MEMBER_BOUNDARY: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "MemberBoundaryConfig",
        vec![with_default(
            "may_depend_on",
            checked(list(Ty::Str), member_globs),
            json!([]),
        )],
    )
});

fn package_pattern(value: &mut Value) -> Result<(), String> {
    let text = value.as_str().unwrap_or_default();
    if !text.split('.').all(is_pattern_segment) {
        return Err("must be a dotted package pattern with whole-segment * wildcards".to_owned());
    }
    Ok(())
}

fn required_modules(value: &mut Value) -> Result<(), String> {
    check_dotted(strings(value))?;
    let names = strings(value).into_iter().map(str::to_owned).collect();
    *value = unique(names);
    Ok(())
}

fn public_modules(value: &mut Value) -> Result<(), String> {
    if value.is_null() {
        return Ok(());
    }
    for entry in strings(value) {
        if entry == "." {
            continue;
        }
        let valid = entry
            .split('.')
            .all(|segment| matches!(segment, "*" | "**") || DOTTED.is_match(segment));
        if !valid {
            return Err("public entries must be relative dotted module patterns or '.'".to_owned());
        }
    }
    let names = strings(value).into_iter().map(str::to_owned).collect();
    *value = unique(names);
    Ok(())
}

fn boundary_after(out: &mut Out) -> Result<(), String> {
    if out.get("count_tests") == Some(&Value::Bool(true))
        && out
            .get("min_importers_to_export")
            .is_none_or(Value::is_null)
    {
        return Err("count_tests requires min_importers_to_export".to_owned());
    }
    Ok(())
}

static PACKAGE_BOUNDARY: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "PackageBoundary",
        vec![
            required("match", checked(Ty::Str, package_pattern)),
            with_default(
                "required",
                checked(list(Ty::Str), required_modules),
                json!([]),
            ),
            with_default(
                "public",
                checked(opt(list(Ty::Str)), public_modules),
                Value::Null,
            ),
            optional("min_imports_to_bundle", Ty::Int(Some(2))),
            optional("min_importers_to_export", Ty::Int(Some(2))),
            with_default("count_tests", Ty::Bool, json!(false)),
            with_default(
                "test_access",
                Ty::Lit(&["public", "mirrored"]),
                json!("public"),
            ),
        ],
    )
    .after(boundary_after)
});

fn canonical_names(value: &mut Value) -> Result<(), String> {
    let map = value.as_object().cloned().unwrap_or_default();
    let names: Vec<&str> = map
        .iter()
        .flat_map(|(key, target)| [key.as_str(), target.as_str().unwrap_or_default()])
        .collect();
    check_dotted(names.iter().copied())?;
    if names.iter().any(|name| !name.contains('.')) {
        return Err("canonical exports require qualified module.symbol names".to_owned());
    }
    Ok(())
}

fn architecture_members(value: &mut Value) -> Result<(), String> {
    let entries = value.as_object().cloned().unwrap_or_default();
    let mut result = Out::new();
    for (key, policy) in entries {
        let pattern = member_pattern(&key)?;
        if result.contains_key(&pattern) {
            return Err(format!("duplicate member glob \"{pattern}\""));
        }
        result.insert(pattern, policy);
    }
    *value = Value::Object(result);
    Ok(())
}

fn wiring_patterns(value: &mut Value) -> Result<(), String> {
    for entry in strings(value) {
        if !entry.split('.').all(is_pattern_segment) {
            return Err(format!(
                "\"{entry}\" must be a dotted module pattern with whole-segment * wildcards"
            ));
        }
    }
    Ok(())
}

pub static ARCHITECTURE: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "ArchitectureConfig",
        vec![
            optional("features", Ty::Model(&FEATURES)),
            with_default("shared", checked(list(Ty::Str), dotted_list), json!([])),
            with_default(
                "composition_root",
                checked(list(Ty::Str), dotted_list),
                json!([]),
            ),
            with_default("wiring", checked(list(Ty::Str), wiring_patterns), json!([])),
            with_default("layers", dict(Ty::Model(&LAYER)), json!({})),
            nested("cross_feature", &CROSS_FEATURE),
            nested("import_graph", &IMPORTS),
            with_default(
                "members",
                checked(dict(Ty::Model(&MEMBER_BOUNDARY)), architecture_members),
                json!({}),
            ),
            with_default("packages", dict(Ty::Model(&PACKAGE_BOUNDARY)), json!({})),
            with_default(
                "canonical_exports",
                checked(dict(Ty::Str), canonical_names),
                json!({}),
            ),
        ],
    )
    .tracked()
});

static IMPORT_STYLE: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "ImportStyleConfig",
        vec![
            with_default(
                "style",
                Ty::Lit(&["relative", "absolute"]),
                json!("relative"),
            ),
            with_default("max_relative_level", Ty::Int(Some(1)), json!(1)),
        ],
    )
});

fn module_slots(value: &mut Value) -> Result<(), String> {
    let values = strings(value);
    let mut sorted = values.clone();
    sorted.sort_unstable();
    sorted.dedup();
    let mut expected = MODULE_ORDER;
    expected.sort_unstable();
    if sorted.len() != values.len() || sorted != expected {
        return Err("module order must contain every module category exactly once".to_owned());
    }
    let position = |slot: &str| values.iter().position(|item| *item == slot);
    if position("public") > position("private") {
        return Err("layout/definition-order requires public before private".to_owned());
    }
    if values[..3] != ["docstring", "future-imports", "imports"]
        || values.last() != Some(&"main-guard")
    {
        return Err(
            "module order must start with docstring, future-imports, imports and end with main-guard"
                .to_owned(),
        );
    }
    Ok(())
}

fn class_slots(value: &mut Value) -> Result<(), String> {
    let values = strings(value);
    let mut sorted = values.clone();
    sorted.sort_unstable();
    sorted.dedup();
    let mut expected = CLASS_ORDER;
    expected.sort_unstable();
    if sorted.len() != values.len() || sorted != expected {
        return Err("class order must contain every class category exactly once".to_owned());
    }
    let position = |slot: &str| values.iter().position(|item| *item == slot);
    if position("public") > position("private") {
        return Err("layout/definition-order requires public before private".to_owned());
    }
    Ok(())
}

static ORDERING: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "OrderingConfig",
        vec![
            with_default(
                "module",
                checked(list(Ty::Lit(&MODULE_ORDER)), module_slots),
                json!(MODULE_ORDER),
            ),
            with_default(
                "class",
                checked(list(Ty::Lit(&CLASS_ORDER)), class_slots),
                json!(CLASS_ORDER),
            ),
            with_default("constants_exempt", Ty::Bool, json!(false)),
            with_default("fix", Ty::Lit(&["off", "safe-only"]), json!("safe-only")),
        ],
    )
});

fn qualified_names(value: &mut Value) -> Result<(), String> {
    for entry in strings(value) {
        if !QUALIFIED.is_match(entry) {
            return Err(format!(
                "{} is not a qualified name such as package.module.Name",
                str_repr(entry)
            ));
        }
    }
    Ok(())
}

static NAVIGATION: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "NavigationConfig",
        vec![
            with_default(
                "namespace_types",
                checked(list(Ty::Str), qualified_names),
                json!([]),
            ),
            with_default(
                "instance_factories",
                checked(list(Ty::Str), qualified_names),
                json!([]),
            ),
        ],
    )
});

static TYPE_DISPATCH: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "TypeDispatchConfig",
        vec![
            with_default("style", Ty::Lit(&["allow", "match"]), json!("match")),
            with_default("min_branches", Ty::Int(Some(2)), json!(3)),
        ],
    )
});

fn error_base(value: &mut Value) -> Result<(), String> {
    if let Some(text) = value.as_str()
        && (!text.contains('.') || !DOTTED.is_match(text))
    {
        return Err(format!(
            "errors.base must be a fully qualified class name: {}",
            str_repr(text)
        ));
    }
    Ok(())
}

fn error_allow(value: &mut Value) -> Result<(), String> {
    for entry in strings(value) {
        let known =
            BUILTIN_EXCEPTIONS.contains(&entry) || (cfg!(windows) && entry == "WindowsError");
        if !known {
            return Err(format!(
                "errors.allow entries must be builtin exception names: {}",
                str_repr(entry)
            ));
        }
    }
    let names = strings(value).into_iter().map(str::to_owned).collect();
    *value = unique(names);
    Ok(())
}

static ERROR_POLICY: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "ErrorPolicy",
        vec![
            with_default("base", checked(opt(Ty::Str), error_base), Value::Null),
            with_default("allow", checked(list(Ty::Str), error_allow), json!([])),
        ],
    )
});

fn unique_options(value: &mut Value) -> Result<(), String> {
    if !value.is_null() {
        let names = strings(value).into_iter().map(str::to_owned).collect();
        *value = unique(names);
    }
    Ok(())
}

fn reexport_names(value: &mut Value) -> Result<(), String> {
    if value.is_null() {
        return Ok(());
    }
    for name in strings(value) {
        if name.contains('.') || !DOTTED.is_match(name) {
            return Err(format!(
                "third_party_reexports entries must be top-level import names: {}",
                str_repr(name)
            ));
        }
    }
    let names = strings(value).into_iter().map(str::to_owned).collect();
    *value = unique(names);
    Ok(())
}

fn base_names(value: &mut Value) -> Result<(), String> {
    let Some(entries) = value.as_object() else {
        return Ok(());
    };
    for (outside, project) in entries {
        let project = project.as_str().unwrap_or_default();
        for name in [outside.as_str(), project] {
            if !name.contains('.') || !name.split('.').all(|part| DOTTED.is_match(part)) {
                return Err(format!(
                    "bases entries must be fully qualified class names: {}",
                    str_repr(name)
                ));
            }
        }
        if outside == project {
            return Err(format!("{outside} cannot replace itself"));
        }
    }
    Ok(())
}

fn policy_fields() -> Vec<Field> {
    vec![
        optional("imports", Ty::Model(&IMPORT_STYLE)),
        optional("reexports", Ty::Lit(&["all", "alias", "allow"])),
        with_default(
            "module_import_exemptions",
            checked(
                opt(list(Ty::Lit(&["facades", "composition_root"]))),
                unique_options,
            ),
            Value::Null,
        ),
        optional(
            "module_docstrings",
            Ty::Lit(&["allow", "forbid", "require"]),
        ),
        optional(
            "future_annotations",
            Ty::Lit(&["allow", "forbid", "when-needed"]),
        ),
        optional("private_modules", Ty::Lit(&["allow", "forbid"])),
        optional("public_attribute_boundary", Ty::Lit(&["class", "module"])),
        optional(
            "private_attribute_boundary",
            Ty::Lit(&["module", "package"]),
        ),
        optional("ordering", Ty::Model(&ORDERING)),
        optional("type_dispatch", Ty::Model(&TYPE_DISPATCH)),
        optional("navigation", Ty::Model(&NAVIGATION)),
        with_default(
            "call_argument_kinds",
            checked(
                opt(Ty::NonEmptyList(Box::new(Ty::Lit(&[
                    "await",
                    "comprehension",
                    "aggregate",
                ])))),
                unique_options,
            ),
            Value::Null,
        ),
        optional("str_enum_values", Ty::Lit(&["auto", "explicit"])),
        with_default(
            "dataclass_options",
            checked(
                opt(list(Ty::Lit(&["frozen", "slots", "kw_only"]))),
                unique_options,
            ),
            Value::Null,
        ),
        with_default(
            "bases",
            checked(opt(dict(Ty::Str)), base_names),
            Value::Null,
        ),
        with_default(
            "third_party_reexports",
            checked(opt(list(Ty::Str)), reexport_names),
            Value::Null,
        ),
        optional("errors", Ty::Model(&ERROR_POLICY)),
        with_default("reasons", dict(Ty::TrimmedReason), json!({})),
    ]
}

/// Move inline `{value, reason}` options into the `reasons` map.
fn read_inline_reasons(raw: &Yaml) -> Result<Yaml, String> {
    let Some(pairs) = raw.as_map() else {
        return Ok(raw.clone());
    };
    let legacy = raw.get("reasons");
    let mut reasons: Vec<(Yaml, Yaml)> = match legacy {
        None => Vec::new(),
        Some(Yaml::Map(entries)) => entries.clone(),
        Some(_) => return Ok(raw.clone()),
    };
    let mut values = pairs.to_vec();
    for name in POLICY_FIELDS {
        let Some(slot) = values
            .iter_mut()
            .find(|(key, _)| key.as_str() == Some(name))
        else {
            continue;
        };
        let inline = slot.1.as_map().is_some_and(|entries| {
            entries
                .iter()
                .any(|(key, _)| matches!(key.as_str(), Some("value" | "reason")))
        });
        if !inline {
            continue;
        }
        if reasons.iter().any(|(key, _)| key.as_str() == Some(name)) {
            return Err(format!("{name} already has a reason in reasons.{name}"));
        }
        let (value, reason) = reasoned_value(&slot.1).map_err(|m| format!("{name}: {m}"))?;
        slot.1 = value;
        reasons.push((Yaml::Str(name.to_owned()), Yaml::Str(reason)));
    }
    if !reasons.is_empty() {
        match values
            .iter_mut()
            .find(|(key, _)| key.as_str() == Some("reasons"))
        {
            Some(slot) => slot.1 = Yaml::Map(reasons),
            None => values.push((Yaml::Str("reasons".to_owned()), Yaml::Map(reasons))),
        }
    }
    Ok(Yaml::Map(values))
}

fn reasoned_value(input: &Yaml) -> Result<(Yaml, String), String> {
    let Some(value) = input.get("value") else {
        return Err("Field required".to_owned());
    };
    let Some(reason) = input.get("reason") else {
        return Err("Field required".to_owned());
    };
    let Some(text) = reason.as_str() else {
        return Err("Input should be a valid string".to_owned());
    };
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err("String should have at least 1 character".to_owned());
    }
    if input
        .as_map()
        .into_iter()
        .flatten()
        .any(|(key, _)| !matches!(key.as_str(), Some("value" | "reason")))
    {
        return Err("Extra inputs are not permitted".to_owned());
    }
    Ok((value.clone(), trimmed.to_owned()))
}

fn reasons_explain_options(out: &mut Out) -> Result<(), String> {
    let names: Vec<String> = out
        .get("reasons")
        .and_then(Value::as_object)
        .map(|reasons| reasons.keys().cloned().collect())
        .unwrap_or_default();
    for name in names {
        let is_option =
            POLICY_FIELDS.contains(&name.as_str()) || RULE_LIMITS.contains(&name.as_str());
        if !is_option || out.get(&name).is_none_or(Value::is_null) {
            return Err(format!(
                "reasons.{name} must explain a style option set here"
            ));
        }
    }
    Ok(())
}

pub fn check_rule_keys(value: &mut Value) -> Result<(), String> {
    for key in value.as_object().into_iter().flatten().map(|(key, _)| key) {
        if key != ALL_RULES && !RULE_CODE.is_match(key) && !RULE_NAME.is_match(key) {
            return Err(format!(
                "\"{key}\" is not a rule code like \"ARC001\", a rule name like \"architecture/layer-boundary\", a family like \"modeling\" or ALL"
            ));
        }
    }
    Ok(())
}

pub fn rules_ty() -> Ty {
    checked(dict(Ty::Rule(&RULE_OVERRIDE)), check_rule_keys)
}

fn plain_rules() -> Ty {
    dict(Ty::Rule(&RULE_OVERRIDE))
}

/// One file's rules map, whose keys the merged map has already checked.
pub fn rules_layer_ty() -> Ty {
    plain_rules()
}

static STYLE_OVERRIDE: LazyLock<ModelDef> = LazyLock::new(|| {
    let mut fields = vec![required("match", checked(Ty::Str, package_pattern))];
    fields.extend(policy_fields());
    fields.push(with_default("rules", plain_rules(), json!({})));
    ModelDef::new("StyleOverride", fields)
        .before(read_inline_reasons)
        .after(reasons_explain_options)
});

static FILE_NAMING: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "FileNamingConfig",
        vec![
            optional("yaml_extension", Ty::Lit(&["yaml", "yml"])),
            optional("compose_basename", Ty::Lit(&["compose", "docker-compose"])),
        ],
    )
});

static STYLE: LazyLock<ModelDef> = LazyLock::new(|| {
    let mut fields = policy_fields();
    fields.push(with_default("forbidden_names", list(Ty::Str), json!([])));
    fields.push(nested("file_naming", &FILE_NAMING));
    fields.push(with_default(
        "overrides",
        dict(Ty::Model(&STYLE_OVERRIDE)),
        json!({}),
    ));
    ModelDef::new("StyleConfig", fields)
        .before(read_inline_reasons)
        .after(reasons_explain_options)
});

fn quality_patterns(value: &mut Value) -> Result<(), String> {
    let mut result = Vec::new();
    for entry in strings(value) {
        result.push(relative_glob(entry).ok_or_else(|| {
            "test quality globs must be nonempty project-relative paths without '..'".to_owned()
        })?);
    }
    *value = unique(result);
    Ok(())
}

fn mirror_pattern(value: &mut Value) -> Result<(), String> {
    let text = value.as_str().unwrap_or_default();
    let mut unknown: Vec<&str> = PLACEHOLDER
        .find_iter(text)
        .map(|found| found.as_str())
        .filter(|found| !matches!(*found, "{path}" | "{module}" | "{root}"))
        .collect();
    unknown.sort_unstable();
    unknown.dedup();
    if !unknown.is_empty() {
        return Err(format!(
            "mirror \"{text}\" has unknown placeholders: {}",
            unknown.join(", ")
        ));
    }
    if text.matches("{module}").count() != 1 || !text.ends_with(".py") {
        return Err(format!(
            "mirror \"{text}\" needs one \"{{module}}\" and must end with \".py\""
        ));
    }
    if text.starts_with('/')
        || text.matches("{path}").count() > 1
        || text.matches("{root}").count() > 1
    {
        return Err(format!(
            "mirror \"{text}\" must be relative, with {{path}} and {{root}} at most once"
        ));
    }
    Ok(())
}

static TESTS: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "TestsConfig",
        vec![
            with_default("layout", Ty::Lit(&["mirror", "none"]), json!("none")),
            with_default(
                "mirror",
                checked(Ty::Str, mirror_pattern),
                json!("{path}/test_{module}.py"),
            ),
            with_default("mirror_exclude", list(Ty::Str), json!([])),
            with_default("mirror_suffixes", Ty::Bool, json!(false)),
            with_default(
                "quality_exclude",
                checked(list(Ty::Str), quality_patterns),
                json!([]),
            ),
            with_default(
                "forbidden_mock_paths",
                checked(list(Ty::Str), quality_patterns),
                json!([]),
            ),
            with_default(
                "assertion_helpers",
                checked(list(Ty::Str), dotted_list),
                json!([]),
            ),
            with_default(
                "timeout_helpers",
                checked(list(Ty::Str), dotted_list),
                json!([]),
            ),
        ],
    )
});

fn ignore_after(out: &mut Out) -> Result<(), String> {
    let empty = |key: &str| {
        out.get(key)
            .and_then(Value::as_array)
            .is_none_or(Vec::is_empty)
    };
    if empty("modules") && empty("paths") {
        return Err("an ignore entry needs `modules` or `paths`".to_owned());
    }
    if out
        .get("reason")
        .and_then(Value::as_str)
        .is_none_or(|reason| reason.trim().is_empty())
    {
        return Err("`reason` must not be empty".to_owned());
    }
    Ok(())
}

static IGNORE_ENTRY: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "IgnoreEntry",
        vec![
            required("rule", Ty::Str),
            with_default("modules", list(Ty::Str), json!([])),
            with_default("paths", list(Ty::Str), json!([])),
            required("reason", Ty::Str),
        ],
    )
    .after(ignore_after)
});

static SUPPRESSIONS: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "SuppressionsConfig",
        vec![with_default("require_reason", Ty::Bool, json!(true))],
    )
});

static MEMBER_PROJECT: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "MemberProjectConfig",
        vec![with_default(
            "exclude",
            list(Ty::StrOr(&SOURCE_EXCLUSION)),
            json!([]),
        )],
    )
});

static MEMBER_STYLE: LazyLock<ModelDef> = LazyLock::new(|| {
    let mut fields = policy_fields();
    fields.push(with_default(
        "overrides",
        dict(Ty::Model(&STYLE_OVERRIDE)),
        json!({}),
    ));
    ModelDef::new("MemberStyleConfig", fields)
        .before(read_inline_reasons)
        .after(reasons_explain_options)
});

fn member_debt(value: &mut Value) -> Result<(), String> {
    let Some(text) = value.as_str() else {
        return Ok(());
    };
    let path = text.trim().replace('\\', "/");
    if path.is_empty()
        || path.starts_with('/')
        || DRIVE.is_match(&path)
        || path.split('/').any(|part| part == "..")
        || path == "."
    {
        return Err("debt must name a member-relative file without '..'".to_owned());
    }
    *value = Value::String(normpath(&path));
    Ok(())
}

/// An empty architecture section counts as absent.
fn member_before(raw: &Yaml) -> Result<Yaml, String> {
    let Some(pairs) = raw.as_map() else {
        return Ok(raw.clone());
    };
    let rewritten = pairs
        .iter()
        .map(|(key, value)| {
            let empty =
                key.as_str() == Some("architecture") && value.as_map().is_some_and(<[_]>::is_empty);
            (key.clone(), if empty { Yaml::Null } else { value.clone() })
        })
        .collect();
    Ok(Yaml::Map(rewritten))
}

fn member_after(out: &mut Out) -> Result<(), String> {
    let style = out
        .get("style")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let packages = out
        .get("architecture")
        .and_then(|value| value.get("packages"))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let overrides = style
        .get("overrides")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let mut structure = style;
    structure.remove("overrides");
    for limit in RULE_LIMITS {
        structure.insert(limit.to_owned(), Value::Null);
    }
    structure.insert(
        "contracts".to_owned(),
        Value::Object(combine_packages(&packages, &overrides)?),
    );
    let rules = out
        .get("rules")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    with_limits(&mut structure, &rules);
    out.insert("__structure__".to_owned(), Value::Object(structure));
    Ok(())
}

pub static MEMBER: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "MemberConfig",
        vec![
            with_default("version", opt(Ty::Version), Value::Null),
            nested("project", &MEMBER_PROJECT),
            nested("style", &MEMBER_STYLE),
            with_default("rules", rules_ty(), json!({})),
            with_default("ignore", list(Ty::Model(&IGNORE_ENTRY)), json!([])),
            optional("architecture", Ty::Model(&ARCHITECTURE)),
            with_default("debt", checked(opt(Ty::Str), member_debt), Value::Null),
        ],
    )
    .before(member_before)
    .after(member_after)
});

fn smelt_after(out: &mut Out) -> Result<(), String> {
    let style = out
        .get("style")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let architecture = out
        .get("architecture")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let packages = architecture
        .get("packages")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let overrides = style
        .get("overrides")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let mut structure = style;
    structure.remove("overrides");
    for limit in RULE_LIMITS {
        structure.insert(limit.to_owned(), Value::Null);
    }
    structure.insert(
        "contracts".to_owned(),
        Value::Object(combine_packages(&packages, &overrides)?),
    );
    structure.insert(
        "canonical_exports".to_owned(),
        architecture
            .get("canonical_exports")
            .cloned()
            .unwrap_or_else(|| json!({})),
    );
    let rules = out
        .get("rules")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    with_limits(&mut structure, &rules);
    out.insert("__structure__".to_owned(), Value::Object(structure));
    Ok(())
}

pub static SMELT: LazyLock<ModelDef> = LazyLock::new(|| {
    ModelDef::new(
        "SmeltConfig",
        vec![
            required("version", Ty::Version),
            Field {
                key: "project",
                ty: Ty::Model(&PROJECT),
                default: FieldDefault::Required,
            },
            nested("architecture", &ARCHITECTURE),
            nested("style", &STYLE),
            nested("tests", &TESTS),
            with_default("rules", rules_ty(), json!({})),
            with_default("ignore", list(Ty::Model(&IGNORE_ENTRY)), json!([])),
            nested("suppressions", &SUPPRESSIONS),
            optional("debt", Ty::Str),
            with_default("members", dict(Ty::Model(&MEMBER)), json!({})),
        ],
    )
    .after(smelt_after)
});

fn combine_packages(boundaries: &Out, overrides: &Out) -> Result<Out, String> {
    let mut names: Vec<&String> = boundaries.keys().collect();
    names.extend(
        overrides
            .keys()
            .filter(|name| !boundaries.contains_key(*name)),
    );
    let mut contracts = Out::new();
    for name in names {
        if name.trim().is_empty() {
            return Err("package policy names must not be empty".to_owned());
        }
        let boundary = boundaries.get(name).and_then(Value::as_object);
        let style = overrides.get(name).and_then(Value::as_object);
        if let (Some(api), Some(over)) = (boundary, style)
            && api.get("match") != over.get("match")
        {
            return Err(format!(
                "package policy \"{name}\" must use the same match in architecture.packages and style.overrides"
            ));
        }
        if let Some(api) = boundary {
            let set = |key: &str| api.get(key).is_some_and(|value| !value.is_null());
            let required = api
                .get("required")
                .and_then(Value::as_array)
                .is_some_and(|items| !items.is_empty());
            if !(required
                || set("public")
                || set("min_imports_to_bundle")
                || set("min_importers_to_export"))
            {
                return Err(format!(
                    "architecture.packages.{name} has no package policy"
                ));
            }
        }
        if let Some(over) = style {
            let configured = POLICY_FIELDS
                .iter()
                .any(|field| over.get(*field).is_some_and(|value| !value.is_null()));
            let has_rules = over
                .get("rules")
                .and_then(Value::as_object)
                .is_some_and(|rules| !rules.is_empty());
            if !(configured || has_rules) {
                return Err(format!("style.overrides.{name} has no style policy"));
            }
        }
        let mut contract = default_contract();
        for source in [boundary, style].into_iter().flatten() {
            for (key, value) in source {
                contract.insert(key.clone(), value.clone());
            }
        }
        if let Some(over) = style {
            let rules = over
                .get("rules")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            with_limits(&mut contract, &rules);
            contract.insert(
                "__style_location__".to_owned(),
                Value::String(format!("style.overrides.{name}")),
            );
            contract.insert("__style_override__".to_owned(), Value::Bool(true));
        } else {
            contract.insert("__style_override__".to_owned(), Value::Bool(false));
        }
        contracts.insert(name.clone(), Value::Object(contract));
    }
    Ok(contracts)
}

/// A package contract with every option unset.
pub fn default_contract() -> Out {
    let mut contract = Out::new();
    for limit in RULE_LIMITS {
        contract.insert(limit.to_owned(), Value::Null);
    }
    contract.insert("match".to_owned(), Value::Null);
    for field in POLICY_FIELDS {
        contract.insert(field.to_owned(), Value::Null);
    }
    contract.insert("reasons".to_owned(), json!({}));
    contract.insert("rules".to_owned(), json!({}));
    contract.insert("required".to_owned(), json!([]));
    contract.insert("public".to_owned(), Value::Null);
    contract.insert("min_imports_to_bundle".to_owned(), Value::Null);
    contract.insert("min_importers_to_export".to_owned(), Value::Null);
    contract.insert("count_tests".to_owned(), json!(false));
    contract.insert("test_access".to_owned(), json!("public"));
    contract
}

/// Copy the limits `rules` set into `policy`, with the reasons given for them.
pub fn with_limits(policy: &mut Out, rules: &Out) {
    for (option, keys) in LIMIT_RULES {
        for key in keys {
            let Some(setting) = rules.get(key).and_then(Value::as_object) else {
                continue;
            };
            let Some(limit) = setting.get("max").filter(|value| !value.is_null()) else {
                continue;
            };
            policy.insert(option.to_owned(), limit.clone());
            if let Some(reason) = setting.get("reason").filter(|value| !value.is_null()) {
                let reasons = policy
                    .entry("reasons".to_owned())
                    .or_insert_with(|| json!({}));
                if let Some(reasons) = reasons.as_object_mut() {
                    reasons.insert(option.to_owned(), reason.clone());
                }
            }
        }
    }
}
