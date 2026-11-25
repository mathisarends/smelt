//! The check report and its machine formats, as
//! `smelt.diagnostics.render.machine` writes them.
use super::finding::{Finding, Severity};
use super::policy::Rule;
use super::pyjson::{indented, sorted_compact};
use serde_json::{Map, Value, json};
use std::collections::{BTreeSet, HashMap};
use std::fmt::Write;

const JSON_SCHEMA_VERSION: u64 = 2;
const SARIF_SCHEMA: &str = "https://json.schemastore.org/sarif-2.1.0.json";
const TOOL_URI: &str = "https://github.com/mathisarends/smelt";
/// The coverage keys `--format json` keeps without `--show-coverage`.
const COVERAGE_SUMMARY: [&str; 8] = [
    "mode",
    "complete",
    "counts",
    "policy_hash",
    "architecture_configured",
    "discovery_errors",
    "omitted_workspace_members",
    "scope",
];
const CYCLE: &str = "ARC004";
const TEST_LOCATION: &str = "TST001";

/// A configuration warning the report lists before the findings.
pub struct Diagnostic {
    pub path: String,
    pub key: String,
    pub message: String,
}

impl Diagnostic {
    fn severity(&self) -> Severity {
        Severity::Warning
    }
}

pub struct Report<'a> {
    pub violations: Vec<Finding>,
    pub modules: u64,
    pub categories: Vec<&'a str>,
    pub rules: Vec<&'a Rule>,
    pub fail_on: Severity,
    pub suppressed: u64,
    pub in_debt: usize,
    pub scope: Vec<String>,
    pub checked_files: usize,
    pub coverage: Map<String, Value>,
    pub project: Value,
    pub diagnostics: Vec<Diagnostic>,
}

impl Report<'_> {
    pub fn count(&self, severity: Severity) -> usize {
        self.violations
            .iter()
            .filter(|violation| violation.severity == severity)
            .count()
            + self
                .diagnostics
                .iter()
                .filter(|diagnostic| diagnostic.severity() == severity)
                .count()
    }

    /// Whether a scoped check of a workspace reports the workspace's totals.
    pub fn workspace_totals(&self) -> bool {
        !self.scope.is_empty()
            && self.coverage.get("scope").and_then(Value::as_str) == Some("workspace")
    }

    pub fn failed(&self) -> bool {
        self.violations
            .iter()
            .any(|violation| violation.severity.at_least(self.fail_on))
            || self
                .diagnostics
                .iter()
                .any(|diagnostic| diagnostic.severity().at_least(self.fail_on))
    }

    fn status(&self) -> &'static str {
        if self.failed() { "failed" } else { "passed" }
    }

    fn summary_json(&self) -> Value {
        json!({
            "errors": self.count(Severity::Error),
            "warnings": self.count(Severity::Warning),
            "hints": self.count(Severity::Hint),
            "modules": self.modules,
            "suppressed": self.suppressed,
            "in_debt": self.in_debt,
        })
    }

    fn repository_prefix(&self) -> &str {
        self.project["repository_prefix"].as_str().unwrap_or("")
    }
}

fn diagnostic_json(diagnostic: &Diagnostic) -> Value {
    json!({
        "path": diagnostic.path,
        "key": diagnostic.key,
        "message": diagnostic.message,
        "severity": diagnostic.severity().value(),
    })
}

pub fn render_json(report: &Report, show_coverage: bool) -> String {
    let coverage: Map<String, Value> = report
        .coverage
        .iter()
        .filter(|(key, _)| show_coverage || COVERAGE_SUMMARY.contains(&key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    let usage_scopes = usage_scopes(&report.violations);
    let violations: Vec<Value> = report
        .violations
        .iter()
        .map(|violation| {
            let mut data = violation.to_json();
            if let Some(Value::Object(expected)) = data.get_mut("expected")
                && !expected.is_empty()
                && let Some(scope) = expected.shift_remove("usage_scope")
            {
                let index = usage_scopes.iter().position(|known| **known == scope);
                expected.insert("usage_scope_index".into(), json!(index));
            }
            data
        })
        .collect();
    let mut document = Map::new();
    document.insert("schema_version".into(), json!(JSON_SCHEMA_VERSION));
    document.insert("status".into(), json!(report.status()));
    document.insert("summary".into(), report.summary_json());
    let mut scope = json!({
        "mode": "full",
        "paths": report.scope,
        "files": report.checked_files,
        "rules": report.rules.iter().map(|rule| rule.code.as_str()).collect::<Vec<_>>(),
    });
    if report.workspace_totals() {
        scope["totals"] = json!("workspace");
    }
    document.insert("scope".into(), scope);
    document.insert("comparison".into(), Value::Null);
    document.insert("coverage".into(), Value::Object(coverage));
    document.insert("project".into(), report.project.clone());
    document.insert("violations".into(), Value::Array(violations));
    document.insert(
        "configuration_diagnostics".into(),
        report.diagnostics.iter().map(diagnostic_json).collect(),
    );
    document.insert(
        "groups".into(),
        Value::Array(group_findings(&report.violations)),
    );
    if !usage_scopes.is_empty() {
        document.insert(
            "usage_scopes".into(),
            Value::Array(usage_scopes.into_iter().cloned().collect()),
        );
    }
    indented(&Value::Object(document)) + "\n"
}

fn sarif_level(severity: Severity) -> &'static str {
    match severity {
        Severity::Error => "error",
        Severity::Warning => "warning",
        Severity::Hint => "note",
    }
}

/// `posixpath.join(prefix, path)`.
fn joined(prefix: &str, path: &str) -> String {
    if prefix.is_empty() || path.starts_with('/') {
        path.to_owned()
    } else if prefix.ends_with('/') {
        format!("{prefix}{path}")
    } else {
        format!("{prefix}/{path}")
    }
}

/// `urllib.parse.quote(text, safe="/")`.
fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || b"_.-~/".contains(&byte) {
            out.push(char::from(byte));
        } else {
            let _ = write!(out, "%{byte:02X}");
        }
    }
    out
}

pub fn render_sarif(report: &Report, version: &str) -> String {
    let rules: Vec<Value> = report
        .rules
        .iter()
        .map(|rule| {
            json!({
                "id": rule.code,
                "name": rule.name,
                "shortDescription": {"text": rule.summary},
                "helpUri": super::finding::docs_url(&rule.code),
                "defaultConfiguration": {"level": sarif_level(rule.default_severity())},
                "properties": {"category": rule.category},
            })
        })
        .collect();
    let index: HashMap<&str, usize> = report
        .rules
        .iter()
        .enumerate()
        .map(|(position, rule)| (rule.code.as_str(), position))
        .collect();
    let prefix = report.repository_prefix();
    let results: Vec<Value> = report
        .violations
        .iter()
        .map(|violation| sarif_result(violation, &index, prefix))
        .collect();
    let notifications: Vec<Value> = report
        .diagnostics
        .iter()
        .map(|diagnostic| {
            json!({
                "level": sarif_level(diagnostic.severity()),
                "message": {"text": format!("{}: {}", diagnostic.key, diagnostic.message)},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": quote(&joined(prefix, &diagnostic.path))}
                    }
                }],
            })
        })
        .collect();
    let mut properties = Map::new();
    properties.insert("mode".into(), json!("full"));
    properties.insert("comparison".into(), Value::Null);
    properties.insert(
        "policyHash".into(),
        report
            .coverage
            .get("policy_hash")
            .cloned()
            .unwrap_or(Value::Null),
    );
    let scopes = usage_scopes(&report.violations);
    if !scopes.is_empty() {
        properties.insert(
            "usageScopes".into(),
            Value::Array(scopes.into_iter().cloned().collect()),
        );
    }
    properties.insert("project".into(), report.project.clone());
    let document = json!({
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "smelt",
                    "version": version,
                    "informationUri": TOOL_URI,
                    "rules": rules,
                }
            },
            "results": results,
            "invocations": [{
                "executionSuccessful": true,
                "toolConfigurationNotifications": notifications,
            }],
            "properties": properties,
        }],
    });
    indented(&document) + "\n"
}

fn sarif_result(violation: &Finding, index: &HashMap<&str, usize>, prefix: &str) -> Value {
    let mut text = violation.message.clone();
    if let Some(hint) = violation.hint.as_deref().filter(|hint| !hint.is_empty()) {
        text.push('\n');
        text.push_str(hint);
    }
    let mut result = Map::new();
    result.insert("ruleId".into(), json!(violation.code));
    result.insert("level".into(), json!(sarif_level(violation.severity)));
    result.insert("message".into(), json!({"text": text}));
    if let Some(position) = index.get(violation.code.as_str()) {
        result.insert("ruleIndex".into(), json!(position));
    }
    if let Some(path) = violation.path.as_deref().filter(|path| !path.is_empty()) {
        let mut region = Map::new();
        if let Some(line) = violation.line.filter(|line| *line != 0) {
            region.insert("startLine".into(), json!(line));
            for (key, value) in [
                ("startColumn", violation.column),
                ("endLine", violation.end_line),
                ("endColumn", violation.end_column),
            ] {
                if let Some(value) = value.filter(|value| *value != 0) {
                    region.insert(key.into(), json!(value));
                }
            }
        }
        let mut location = Map::new();
        location.insert(
            "artifactLocation".into(),
            json!({"uri": quote(&joined(prefix, path))}),
        );
        if !region.is_empty() {
            location.insert("region".into(), Value::Object(region));
        }
        result.insert(
            "locations".into(),
            json!([{"physicalLocation": Value::Object(location)}]),
        );
    }
    Value::Object(result)
}

fn github_level(severity: Severity) -> &'static str {
    match severity {
        Severity::Error => "error",
        Severity::Warning => "warning",
        Severity::Hint => "notice",
    }
}

fn escape_data(value: &str) -> String {
    value
        .replace('%', "%25")
        .replace('\r', "%0D")
        .replace('\n', "%0A")
}

fn escape_property(value: &str) -> String {
    escape_data(value).replace(':', "%3A").replace(',', "%2C")
}

pub fn render_github(report: &Report) -> String {
    let prefix = report.repository_prefix();
    let mut lines: Vec<String> = report
        .violations
        .iter()
        .map(|violation| github_line(violation, prefix))
        .collect();
    lines.extend(report.diagnostics.iter().map(|diagnostic| {
        format!(
            "::{} file={},title=Smelt configuration::{}",
            github_level(diagnostic.severity()),
            escape_property(&joined(prefix, &diagnostic.path)),
            escape_data(&format!("{}: {}", diagnostic.key, diagnostic.message)),
        )
    }));
    if !usage_scopes(&report.violations).is_empty() {
        lines.push("Usage limits: excluded files, unselected packages and external/dynamic consumers were not assessed; review them before renaming.".to_owned());
    }
    lines.push(format!(
        "smelt: {} errors, {} warnings, {} hints, {} modules",
        report.count(Severity::Error),
        report.count(Severity::Warning),
        report.count(Severity::Hint),
        report.modules,
    ));
    lines.join("\n") + "\n"
}

fn github_line(violation: &Finding, prefix: &str) -> String {
    let mut properties: Vec<String> = Vec::new();
    if let Some(path) = violation.path.as_deref().filter(|path| !path.is_empty()) {
        properties.push(format!("file={}", escape_property(&joined(prefix, path))));
    }
    for (key, value) in [
        ("line", violation.line),
        ("col", violation.column),
        ("endLine", violation.end_line),
        ("endColumn", violation.end_column),
    ] {
        if let Some(value) = value.filter(|value| *value != 0) {
            properties.push(format!("{key}={value}"));
        }
    }
    properties.push(format!(
        "title={}",
        escape_property(&format!("{} {}", violation.code, violation.rule))
    ));
    let mut message = violation.message.clone();
    if let Some(hint) = violation.hint.as_deref().filter(|hint| !hint.is_empty()) {
        message.push_str("\nHint: ");
        message.push_str(hint);
    }
    format!(
        "::{} {}::{}",
        github_level(violation.severity),
        properties.join(","),
        escape_data(&message)
    )
}

/// `finding_usage_scopes`: each distinct usage scope the findings name.
pub fn usage_scopes(violations: &[Finding]) -> Vec<&Value> {
    let mut scopes: Vec<&Value> = Vec::new();
    for violation in violations {
        if let Some(scope) = violation
            .expected_get("usage_scope")
            .filter(|scope| scope.is_object())
            && !scopes.contains(&scope)
        {
            scopes.push(scope);
        }
    }
    scopes
}

fn is_transitive(violation: &Finding) -> bool {
    violation.code != CYCLE && violation.import_chain.len() > 1
}

fn facade_key(violation: &Finding) -> Option<String> {
    is_transitive(violation).then(|| violation.import_chain[0].imported.clone())
}

fn edge_of(violation: &Finding) -> Option<&str> {
    violation.edge.as_deref().filter(|edge| !edge.is_empty())
}

fn edge_key(violation: &Finding) -> Option<String> {
    if violation.code == CYCLE {
        return None;
    }
    edge_of(violation).map(|edge| format!("{} {edge}", violation.code))
}

fn cycle_key(violation: &Finding) -> Option<String> {
    (violation.code == CYCLE)
        .then(|| edge_of(violation).map(str::to_owned))
        .flatten()
}

fn parent(path: &str) -> &str {
    path.rfind('/').map_or("", |index| &path[..index])
}

fn test_move_key(violation: &Finding) -> Option<String> {
    let path = violation.path.as_deref().filter(|path| !path.is_empty())?;
    if violation.code != TEST_LOCATION {
        return None;
    }
    let target = violation.expected_get("path")?.as_str()?;
    Some(format!("{}/ -> {}/", parent(path), parent(target)))
}

/// The ordering findings the text report merges per file.
pub fn merged_ordering(violation: &Finding) -> bool {
    violation.code == "LAY004"
        && violation.expected_get("scope").and_then(Value::as_str) == Some("module")
        && matches!(
            violation.message.as_str(),
            "imports appear after later definition groups"
                | "future-imports appear after later definition groups"
        )
}

fn ordering_key(violation: &Finding) -> Option<String> {
    merged_ordering(violation).then(|| {
        format!(
            "{}: {}",
            violation.path.as_deref().unwrap_or("None"),
            violation.message
        )
    })
}

fn shared_helper_key(violation: &Finding) -> Option<String> {
    let helper = violation
        .expected_get("helper")
        .filter(|helper| helper.is_object())?;
    let location = violation
        .expected_get("operation_location")
        .filter(|location| location.is_object())?;
    if violation.code != "TST008"
        || violation.expected_get("kind").and_then(Value::as_str) != Some("unbounded-wait")
    {
        return None;
    }
    let field = |key: &str| violation.expected_get(key).cloned().unwrap_or(Value::Null);
    Some(sorted_compact(&json!([
        violation.path,
        location,
        helper.get("name").cloned().unwrap_or(Value::Null),
        helper
            .get("definition_line")
            .cloned()
            .unwrap_or(Value::Null),
        field("operation"),
        field("kind"),
        field("provenance"),
        violation.severity.value(),
        violation.hint,
    ])))
}

type GroupKey = fn(&Finding) -> Option<String>;

/// `group_findings`: facades, edges, cycles and test moves behind several findings.
pub fn group_findings(violations: &[Finding]) -> Vec<Value> {
    let kinds: [(&str, GroupKey, usize); 6] = [
        ("facade", facade_key, 1),
        ("edge", edge_key, 2),
        ("cycle", cycle_key, 1),
        ("test_move", test_move_key, 2),
        ("ordering", ordering_key, 2),
        ("shared_helper", shared_helper_key, 2),
    ];
    let mut groups: Vec<(usize, &str, String, Value)> = Vec::new();
    for (kind, key, minimum) in kinds {
        let mut members: Vec<(String, Vec<&Finding>)> = Vec::new();
        let mut positions: HashMap<String, usize> = HashMap::new();
        for violation in violations {
            let Some(value) = key(violation) else {
                continue;
            };
            match positions.get(&value) {
                Some(position) => members[*position].1.push(violation),
                None => {
                    positions.insert(value.clone(), members.len());
                    members.push((value, vec![violation]));
                }
            }
        }
        for (value, found) in members {
            if found.len() >= minimum {
                let group = group(kind, &value, &found);
                groups.push((found.len(), kind, value, group));
            }
        }
    }
    groups.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| left.1.cmp(right.1))
            .then_with(|| left.2.cmp(&right.2))
    });
    groups.into_iter().map(|(_, _, _, group)| group).collect()
}

fn group(kind: &str, key: &str, found: &[&Finding]) -> Value {
    let codes: BTreeSet<&str> = found
        .iter()
        .map(|violation| violation.code.as_str())
        .collect();
    let mut group = Map::new();
    group.insert("kind".into(), json!(kind));
    group.insert("key".into(), json!(key));
    group.insert("count".into(), json!(found.len()));
    group.insert("codes".into(), json!(codes));
    let mut ids: Vec<String> = found
        .iter()
        .map(|violation| violation.finding_id())
        .collect();
    group.insert("violations".into(), json!(ids));
    if kind == "facade" || kind == "edge" {
        let transitive = found
            .iter()
            .filter(|violation| is_transitive(violation))
            .count();
        group.insert("direct".into(), json!(found.len() - transitive));
        group.insert("transitive".into(), json!(transitive));
    }
    if kind == "facade" {
        let edges: BTreeSet<&str> = found
            .iter()
            .filter_map(|violation| edge_of(violation))
            .collect();
        group.insert("edges".into(), json!(edges));
    }
    if kind == "cycle" {
        let first = found[0];
        group.insert(
            "witness".into(),
            Value::Array(
                first
                    .import_chain
                    .iter()
                    .map(|link| link.to_json())
                    .collect(),
            ),
        );
        let only = first
            .expected_get("type_checking_only")
            .is_some_and(super::policy::truthy);
        group.insert("type_checking_only".into(), json!(only));
    }
    if kind == "shared_helper" {
        let first = found[0];
        let expected = |key: &str| first.expected_get(key).cloned().unwrap_or(Value::Null);
        group.insert("operation".into(), expected("operation"));
        let mut location = Map::new();
        location.insert("path".into(), json!(first.path));
        for (key, value) in expected("operation_location")
            .as_object()
            .into_iter()
            .flatten()
        {
            location.insert(key.clone(), value.clone());
        }
        group.insert("location".into(), Value::Object(location));
        let helper = expected("helper");
        group.insert(
            "helper".into(),
            json!({"name": helper["name"], "definition_line": helper["definition_line"]}),
        );
        group.insert("repair".into(), json!(first.hint));
        let mut tests: Vec<Value> = found
            .iter()
            .map(|violation| {
                let helper = violation
                    .expected_get("helper")
                    .cloned()
                    .unwrap_or(Value::Null);
                json!({
                    "test": violation.expected_get("test").cloned().unwrap_or(Value::Null),
                    "call_line": helper["call_line"],
                    "call_lines": helper["call_lines"],
                    "violation": violation.finding_id(),
                })
            })
            .collect();
        tests.sort_by(|left, right| {
            let line = |test: &Value| test["call_line"].as_u64().unwrap_or(0);
            line(left)
                .cmp(&line(right))
                .then_with(|| left["test"].as_str().cmp(&right["test"].as_str()))
                .then_with(|| left["violation"].as_str().cmp(&right["violation"].as_str()))
        });
        group.insert("affected_tests".into(), Value::Array(tests));
        ids.sort();
        group.insert("violations".into(), json!(ids));
    }
    Value::Object(group)
}
