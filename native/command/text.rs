//! The text and concise reports, as `smelt.diagnostics.render.text` writes them.
use super::finding::{Finding, Severity};
use super::pytext::{display, fill, is_space, plural, squash};
use super::report::{Report, group_findings, merged_ordering};
use serde_json::{Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet};

const WIDTH: usize = 88;
const TOP_EDGES: usize = 5;
/// Longer listings end with a per-rule breakdown.
const STATISTICS_THRESHOLD: usize = 10;
/// Keys of `expected` that only machine readers need.
const MACHINE_ONLY: [&str; 8] = [
    "cycle",
    "members",
    "cyclic_edges",
    "subject",
    "evidence",
    "usage_scope",
    "fix_blocker",
    "affected_lines",
];
const BOLD: &str = "\x1b[1m";
const GREEN: &str = "\x1b[32m";
const RED: &str = "\x1b[31m";
const YELLOW: &str = "\x1b[33m";
const RESET: &str = "\x1b[0m";

fn severity_color(severity: Severity) -> &'static str {
    match severity {
        Severity::Error => "\x1b[31m",
        Severity::Warning => "\x1b[33m",
        Severity::Hint => "\x1b[36m",
    }
}

/// The text of a line of a project file; `Err` where the Python report fails.
pub type LineReader<'a> = dyn FnMut(&str, u64) -> Result<String, ()> + 'a;

struct Style {
    color: bool,
    concise: bool,
}

impl Style {
    fn paint(&self, text: &str, code: &str) -> String {
        if self.color {
            format!("{code}{text}{RESET}")
        } else {
            text.to_owned()
        }
    }
}

pub fn render(
    report: &Report,
    read_line: &mut LineReader,
    color: bool,
    concise: bool,
    show_hints: bool,
) -> Result<String, ()> {
    let style = Style { color, concise };
    let shown: Vec<&Finding> = report
        .violations
        .iter()
        .filter(|violation| show_hints || violation.severity != Severity::Hint)
        .collect();
    let mut blocks: Vec<String> = report
        .diagnostics
        .iter()
        .map(|diagnostic| {
            format!(
                "warning: {}: {}: {}",
                diagnostic.path, diagnostic.key, diagnostic.message
            )
        })
        .collect();
    let (findings, shared) = finding_blocks(&shown, read_line, &style)?;
    blocks.extend(findings);
    let mut lines: Vec<String> = Vec::new();
    if !blocks.is_empty() {
        lines.push(blocks.join(if concise { "\n" } else { "\n\n" }));
        lines.push(String::new());
    }
    if !shared.is_empty() {
        lines.extend(shared_repair_lines(&shared, &style));
        lines.push(String::new());
    }
    let edges = repeated_edges(&shown);
    if !edges.is_empty() {
        lines.extend(edges);
        lines.push(String::new());
    }
    let facades = facades(&shown);
    if !facades.is_empty() {
        lines.extend(facades);
        lines.push(String::new());
    }
    let codes: HashSet<&str> = shown
        .iter()
        .map(|violation| violation.code.as_str())
        .collect();
    if shown.len() > STATISTICS_THRESHOLD && codes.len() > 1 {
        lines.push(statistics(&shown, &style));
        lines.push(String::new());
    }
    if !report.scope.is_empty() {
        lines.push(format!(
            "Scope: {} ({} analyzed source/test files)",
            report.scope.join(", "),
            report.checked_files
        ));
    }
    if report
        .comparison
        .as_ref()
        .is_some_and(|comparison| comparison.get("mode").and_then(Value::as_str) == Some("staged"))
    {
        lines.push("Scope: staged Git index; new findings since the compared commit.".to_owned());
    }
    if !concise
        && shown.iter().any(|violation| {
            violation
                .expected_get("usage_scope")
                .is_some_and(super::pyjson::truthy)
        })
    {
        lines.push("Usage limits: excluded files (including maintainer scripts), unselected packages and external/dynamic consumers were not assessed; review them before renaming.".to_owned());
    }
    let coverage = &report.coverage;
    if let Some(complete) = coverage.get("complete") {
        let complete = super::pyjson::truthy(complete);
        let attention = coverage
            .get("architecture_configured")
            .is_none_or(super::pyjson::truthy)
            || coverage.get("mode").and_then(Value::as_str) == Some("strict");
        if !(concise && complete) && attention {
            let counts = coverage.get("counts");
            let count = |key: &str| {
                counts
                    .and_then(|counts| counts.get(key))
                    .map_or_else(|| "0".to_owned(), display)
            };
            lines.push(format!(
                "{}: {}; {} uncovered, {} unclassified sources",
                if report.workspace_totals() {
                    "Workspace policy coverage"
                } else {
                    "Policy coverage"
                },
                format_args!(
                    "{} ({})",
                    if complete { "complete" } else { "incomplete" },
                    coverage
                        .get("mode")
                        .map_or_else(|| "configured".to_owned(), display)
                ),
                count("uncovered"),
                count("unclassified"),
            ));
            for error in coverage
                .get("discovery_errors")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                lines.push(format!("Coverage discovery: {}", display(error)));
            }
        }
    }
    lines.push(summary(report, &style, show_hints));
    Ok(lines.join("\n") + "\n")
}

type Repair = (String, String);

fn finding_blocks(
    shown: &[&Finding],
    read_line: &mut LineReader,
    style: &Style,
) -> Result<(Vec<String>, BTreeMap<Repair, usize>), ()> {
    let owned: Vec<Finding> = shown.iter().map(|violation| (*violation).clone()).collect();
    let helper_groups = group_findings(&owned);
    let mut groups: HashMap<(String, Value), &Value> = HashMap::new();
    for group in helper_groups
        .iter()
        .filter(|group| group["kind"] == "shared_helper")
    {
        for id in group["violations"].as_array().into_iter().flatten() {
            groups.insert(
                (
                    id.as_str().unwrap_or_default().to_owned(),
                    group["location"]["column"].clone(),
                ),
                group,
            );
        }
    }
    let displayed: Vec<(Finding, Option<&Value>)> = group_ordering(group_unclassified(shown))
        .into_iter()
        .map(|violation| {
            let column = violation
                .expected_get("operation_location")
                .and_then(|location| location.get("column"))
                .cloned()
                .unwrap_or(Value::Null);
            let group = groups.get(&(violation.finding_id(), column)).copied();
            (violation, group)
        })
        .collect();
    let mut shared: BTreeMap<Repair, usize> = BTreeMap::new();
    for (violation, group) in &displayed {
        if group.is_none()
            && let Some(hint) = violation.hint.as_deref().filter(|hint| !hint.is_empty())
        {
            *shared
                .entry((violation.code.clone(), hint.to_owned()))
                .or_default() += 1;
        }
    }
    shared.retain(|_, count| *count > 1);
    let mut rendered: HashSet<String> = HashSet::new();
    let mut blocks = Vec::new();
    for (violation, group) in &displayed {
        match group {
            None => {
                let repair = (
                    violation.code.clone(),
                    violation.hint.clone().unwrap_or_default(),
                );
                blocks.push(render_violation(
                    violation,
                    read_line,
                    style,
                    !shared.contains_key(&repair),
                )?);
            }
            Some(group) => {
                let key = group["key"].as_str().unwrap_or_default().to_owned();
                if !rendered.insert(key) {
                    continue;
                }
                let mut common = violation.clone();
                common.expected = None;
                common.message = format!(
                    "{} has no recognized deadline for {} ({} affected tests)",
                    display(&group["helper"]["name"]),
                    display(&group["operation"]),
                    display(&group["count"]),
                );
                let mut lines = vec![render_violation(&common, read_line, style, true)?];
                for test in group["affected_tests"].as_array().into_iter().flatten() {
                    for line in test["call_lines"].as_array().into_iter().flatten() {
                        lines.push(format!(
                            "  Affected: {}:{} {} [{}]",
                            violation.path.as_deref().unwrap_or("None"),
                            display(line),
                            display(&test["test"]),
                            display(&test["violation"]),
                        ));
                    }
                }
                blocks.push(lines.join("\n"));
            }
        }
    }
    Ok((blocks, shared))
}

/// `_group_ordering`: one block per file for misplaced imports.
fn group_ordering(violations: Vec<Finding>) -> Vec<Finding> {
    let mut groups: HashMap<(Option<String>, String), Vec<usize>> = HashMap::new();
    for (index, violation) in violations.iter().enumerate() {
        if merged_ordering(violation) {
            groups
                .entry((violation.path.clone(), violation.message.clone()))
                .or_default()
                .push(index);
        }
    }
    let mut result = Vec::new();
    for (index, violation) in violations.iter().enumerate() {
        let group = groups
            .get(&(violation.path.clone(), violation.message.clone()))
            .map_or(&[][..], Vec::as_slice);
        if violation.code != "LAY004" || group.len() <= 1 {
            result.push(violation.clone());
        } else if group[0] == index {
            let affected: Vec<String> = group
                .iter()
                .map(|member| {
                    violations[*member]
                        .line
                        .map_or_else(|| "None".to_owned(), |line| line.to_string())
                })
                .collect();
            let mut merged = violation.clone();
            merged.message = format!("{} (lines {})", violation.message, affected.join(", "));
            result.push(merged);
        }
    }
    result
}

/// `posixpath.commonpath` of relative paths.
fn common_path(paths: &[&str]) -> String {
    let split: Vec<Vec<&str>> = paths
        .iter()
        .map(|path| {
            path.split('/')
                .filter(|part| !part.is_empty() && *part != ".")
                .collect()
        })
        .collect();
    let Some(first) = split.first() else {
        return String::new();
    };
    let mut common = first.len();
    for parts in &split[1..] {
        common = common.min(
            first
                .iter()
                .zip(parts)
                .take_while(|(left, right)| left == right)
                .count(),
        );
    }
    first[..common].join("/")
}

/// `_group_unclassified`: one block per rule for coverage gaps.
fn group_unclassified(violations: &[&Finding]) -> Vec<Finding> {
    let gap = |violation: &Finding| {
        matches!(violation.code.as_str(), "ARC012" | "ARC013")
            && violation
                .path
                .as_deref()
                .is_some_and(|path| !path.is_empty())
    };
    let mut groups: HashMap<(&str, Severity, Option<&str>), Vec<usize>> = HashMap::new();
    for (index, violation) in violations.iter().enumerate() {
        if gap(violation) {
            groups
                .entry((
                    violation.code.as_str(),
                    violation.severity,
                    violation.hint.as_deref(),
                ))
                .or_default()
                .push(index);
        }
    }
    let mut result = Vec::new();
    for (index, violation) in violations.iter().enumerate() {
        if !gap(violation) {
            result.push((*violation).clone());
            continue;
        }
        let group = &groups[&(
            violation.code.as_str(),
            violation.severity,
            violation.hint.as_deref(),
        )];
        if group[0] != index {
            continue;
        }
        if group.len() > 1 {
            let paths: Vec<&str> = group
                .iter()
                .filter_map(|member| violations[*member].path.as_deref())
                .collect();
            let mut grouped = (*violation).clone();
            grouped.path = Some(common_path(&paths));
            grouped.message = if violation.code == "ARC013" {
                format!(
                    "{} source files have incomplete policy coverage; --format json lists individual paths",
                    group.len()
                )
            } else {
                format!(
                    "{} modules have no architecture classification; --format json lists individual paths",
                    group.len()
                )
            };
            result.push(grouped);
        } else {
            result.push((*violation).clone());
        }
    }
    result
}

/// Count values in first-seen order, as `collections.Counter` does.
fn counted<K: Eq + std::hash::Hash + Clone>(keys: impl Iterator<Item = K>) -> Vec<(K, usize)> {
    let mut positions: HashMap<K, usize> = HashMap::new();
    let mut counts: Vec<(K, usize)> = Vec::new();
    for key in keys {
        match positions.get(&key) {
            Some(position) => counts[*position].1 += 1,
            None => {
                positions.insert(key.clone(), counts.len());
                counts.push((key, 1));
            }
        }
    }
    counts
}

fn repeated_edges(violations: &[&Finding]) -> Vec<String> {
    let counts = counted(violations.iter().filter_map(|violation| {
        violation
            .edge
            .as_deref()
            .filter(|edge| !edge.is_empty())
            .map(|edge| (violation.code.as_str(), edge))
    }));
    let mut repeated: Vec<(usize, &str, &str)> = counts
        .into_iter()
        .filter(|(_, count)| *count > 1)
        .map(|((code, edge), count)| (count, code, edge))
        .collect();
    if repeated.is_empty() {
        return Vec::new();
    }
    repeated.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| left.1.cmp(right.1))
            .then_with(|| left.2.cmp(right.2))
    });
    let width = repeated[0].0.to_string().len();
    let mut lines = vec![
        "Repeated dependency edges (often one decision or fix each; JSON \"edge\"):".to_owned(),
    ];
    for (count, code, edge) in repeated.iter().take(TOP_EDGES) {
        lines.push(format!("  {count:>width$}x {code} {edge}"));
    }
    if repeated.len() > TOP_EDGES {
        lines.push(format!("  … {} more", repeated.len() - TOP_EDGES));
    }
    lines
}

fn facades(violations: &[&Finding]) -> Vec<String> {
    let owned: Vec<Finding> = violations
        .iter()
        .map(|violation| (*violation).clone())
        .collect();
    let groups: Vec<Value> = group_findings(&owned)
        .into_iter()
        .filter(|group| group["kind"] == "facade")
        .collect();
    if groups.is_empty() {
        return Vec::new();
    }
    let width = display(&groups[0]["count"]).len();
    let mut lines =
        vec!["Indirect findings by the module relaying them (JSON \"groups\"):".to_owned()];
    for group in groups.iter().take(TOP_EDGES) {
        let edges: Vec<String> = group["edges"]
            .as_array()
            .into_iter()
            .flatten()
            .map(display)
            .collect();
        lines.push(format!(
            "  {:>width$}x {} ({})",
            display(&group["count"]),
            display(&group["key"]),
            edges.join(", ")
        ));
    }
    if groups.len() > TOP_EDGES {
        lines.push(format!("  … {} more", groups.len() - TOP_EDGES));
    }
    lines
}

fn statistics(violations: &[&Finding], style: &Style) -> String {
    let mut ordered = counted(violations.iter().map(|violation| {
        (
            violation.code.as_str(),
            violation.rule.as_str(),
            violation.severity,
        )
    }));
    ordered.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.0.cmp(right.0.0)));
    let width = ordered[0].1.to_string().len();
    let name_width = ordered
        .iter()
        .map(|((_, rule, _), _)| rule.chars().count())
        .max()
        .unwrap_or(0);
    ordered
        .iter()
        .map(|((code, rule, severity), count)| {
            format!(
                "{count:>width$}  {} {rule:<name_width$}  {}",
                style.paint(code, BOLD),
                style.paint(
                    &format!("[{}]", severity.value()),
                    severity_color(*severity)
                ),
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn shared_repair_lines(shared: &BTreeMap<Repair, usize>, style: &Style) -> Vec<String> {
    let mut lines = vec!["Repairs shared by several findings (shown once):".to_owned()];
    for ((code, hint), count) in shared {
        let label = format!("{code} ({count}x): ");
        let text = squash(hint);
        lines.push(if style.concise {
            format!("  {label}{text}")
        } else {
            wrap(&label, &text)
        });
    }
    lines
}

fn render_violation(
    violation: &Finding,
    read_line: &mut LineReader,
    style: &Style,
    show_hint: bool,
) -> Result<String, ()> {
    let mut location = violation
        .path
        .clone()
        .filter(|path| !path.is_empty())
        .unwrap_or_else(|| "<project>".to_owned());
    let line = violation.line.filter(|line| *line != 0);
    let column = violation.column.filter(|column| *column != 0);
    if let Some(line) = line {
        location.push_str(&format!(":{line}"));
        if let Some(column) = column {
            location.push_str(&format!(":{column}"));
        }
    }
    let hint = violation
        .hint
        .as_deref()
        .filter(|hint| show_hint && !hint.is_empty());
    let marker = fix_marker(violation);
    if style.concise {
        if line.is_some() && column.is_none() {
            location.push_str(":1");
        }
        let hint = hint.map_or_else(String::new, |hint| format!(" -> {}", squash(hint)));
        let tag = if marker.is_empty() {
            String::new()
        } else {
            format!(" {marker}")
        };
        return Ok(format!(
            "{location} {} {}{tag} {}{hint}",
            violation.code,
            violation.rule,
            squash(&violation.message)
        ));
    }
    let severity = style.paint(
        &format!("[{}]", violation.severity.value()),
        severity_color(violation.severity),
    );
    let mut header = format!(
        "{}  {} {}  {severity}",
        style.paint(&location, BOLD),
        style.paint(&violation.code, BOLD),
        violation.rule
    );
    if !marker.is_empty() {
        header.push(' ');
        header.push_str(&style.paint(marker, GREEN));
    }
    let mut out = vec![header, format!("  {}", violation.message)];
    out.extend(snippet(violation, read_line, style)?);
    out.extend(chain(violation));
    if let Some(expected) = violation
        .expected
        .as_ref()
        .filter(|expected| !expected.is_empty())
    {
        out.extend(expected_lines(violation, expected));
    }
    if let Some(hint) = hint {
        out.push(wrap("Hint: ", hint));
    }
    Ok(out.join("\n"))
}

fn fix_marker(violation: &Finding) -> &'static str {
    match violation.applicability() {
        Some("safe") => "[fix]",
        Some("unsafe") => "[unsafe fix]",
        Some("display") => "[suggestion]",
        _ => "",
    }
}

fn chain(violation: &Finding) -> Vec<String> {
    let links = &violation.import_chain;
    if links.is_empty() {
        return Vec::new();
    }
    let connected = links
        .windows(2)
        .all(|pair| pair[0].imported == pair[1].importer);
    if connected {
        let mut modules = vec![links[0].importer.as_str()];
        modules.extend(links.iter().map(|link| link.imported.as_str()));
        return vec![wrap("Chain: ", &modules.join(" → "))];
    }
    let mut lines = vec!["  Imports:".to_owned()];
    for link in links {
        let place = link
            .line
            .filter(|line| *line != 0)
            .map_or_else(String::new, |line| format!(" (line {line})"));
        lines.push(format!("    {} → {}{place}", link.importer, link.imported));
    }
    lines
}

fn snippet(
    violation: &Finding,
    read_line: &mut LineReader,
    style: &Style,
) -> Result<Vec<String>, ()> {
    let (Some(path), Some(line)) = (
        violation.path.as_deref().filter(|path| !path.is_empty()),
        violation.line.filter(|line| *line != 0),
    ) else {
        return Ok(Vec::new());
    };
    let raw = read_line(path, line)?;
    if raw.chars().all(is_space) {
        return Ok(Vec::new());
    }
    let stripped = raw.trim_start_matches(is_space);
    let indent = raw.chars().count() - stripped.chars().count();
    let mut lines = vec![format!("    {}", stripped.trim_end_matches(is_space))];
    if let (Some(column), Some(end_column)) = (
        violation.column.filter(|column| *column != 0),
        violation.end_column.filter(|column| *column != 0),
    ) && violation.end_line.is_none_or(|end_line| end_line == line)
    {
        let start = (column as i64 - 1 - indent as i64).max(0) as usize;
        let width = (end_column as i64 - column as i64).max(1) as usize;
        let carets = style.paint(&"^".repeat(width), severity_color(violation.severity));
        lines.push(format!("    {}{carets}", " ".repeat(start)));
    }
    Ok(lines)
}

fn empty(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Array(items) => items.is_empty(),
        Value::Object(entries) => entries.is_empty(),
        _ => false,
    }
}

fn expected_lines(violation: &Finding, expected: &Map<String, Value>) -> Vec<String> {
    let mut lines = Vec::new();
    let layer = violation.layer.as_deref().filter(|layer| !layer.is_empty());
    for (key, value) in expected {
        if empty(value) && key != "may_depend_on" {
            continue;
        }
        if key == "may_depend_on" && layer.is_some() {
            let targets = match value {
                Value::Array(items) if !items.is_empty() => {
                    items.iter().map(display).collect::<Vec<_>>().join(", ")
                }
                Value::String(text) if !text.is_empty() => text
                    .chars()
                    .map(String::from)
                    .collect::<Vec<_>>()
                    .join(", "),
                other if super::pyjson::truthy(other) => display(other),
                _ => "(nothing)".to_owned(),
            };
            lines.push(format!(
                "  Allowed: {} → {targets}",
                layer.unwrap_or_default()
            ));
        } else if MACHINE_ONLY.contains(&key.as_str())
            || key.starts_with("automatic_")
            || (key == "fix_reason"
                && violation
                    .hint
                    .as_deref()
                    .is_some_and(|hint| !hint.is_empty() && hint.contains(&display(value))))
        {
            continue;
        } else if key == "candidates" {
            lines.extend(candidate_lines(value));
        } else if key == "path" || key == "expected_path" {
            lines.push(format!("  Expected: {}", display(value)));
        } else {
            lines.push(wrap(
                &format!("{}: ", capitalize(&key.replace('_', " "))),
                &format_value(value),
            ));
        }
    }
    lines
}

/// `str.capitalize()`: the first character upper case, the rest lower case.
fn capitalize(text: &str) -> String {
    let mut characters = text.chars();
    match characters.next() {
        Some(first) => first
            .to_uppercase()
            .chain(characters.flat_map(char::to_lowercase))
            .collect(),
        None => String::new(),
    }
}

fn candidate_lines(candidates: &Value) -> Vec<String> {
    let mut lines = vec!["  Candidates:".to_owned()];
    let joined = |value: Option<&Value>| match value {
        Some(Value::Array(items)) => items.iter().map(display).collect::<Vec<_>>().join(", "),
        _ => String::new(),
    };
    for candidate in candidates.as_array().into_iter().flatten() {
        let names = joined(candidate.get("imports"));
        let via = joined(candidate.get("via"));
        let detail = if via.is_empty() {
            names
        } else {
            format!("{names} via {via}")
        };
        let source = display(&candidate["source"]);
        let test_path = display(&candidate["test_path"]);
        lines.push(if detail.is_empty() {
            format!("    {source} → {test_path}")
        } else {
            format!("    {source} ({detail}) → {test_path}")
        });
    }
    lines
}

fn format_value(value: &Value) -> String {
    match value {
        Value::Bool(flag) => if *flag { "yes" } else { "no" }.to_owned(),
        Value::Array(items) if items.is_empty() => "(none)".to_owned(),
        Value::Array(items) => items.iter().map(display).collect::<Vec<_>>().join(", "),
        Value::Object(entries) => entries
            .iter()
            .map(|(key, item)| format!("{key}: {}", format_value(item)))
            .collect::<Vec<_>>()
            .join("; "),
        other => display(other),
    }
}

fn wrap(label: &str, text: &str) -> String {
    fill(
        text,
        WIDTH,
        &format!("  {label}"),
        &" ".repeat(label.chars().count() + 2),
    )
}

fn summary(report: &Report, style: &Style, show_hints: bool) -> String {
    let errors = report.count(Severity::Error);
    let warnings = report.count(Severity::Warning);
    let hints = report.count(Severity::Hint);
    let mut hint_text = plural(hints, "hint");
    if !show_hints
        && report
            .violations
            .iter()
            .any(|violation| violation.severity == Severity::Hint)
    {
        hint_text.push_str(" (use --show-hints)");
    }
    let mut tail: Vec<String> = Vec::new();
    let scope = if report.workspace_totals() {
        " in workspace"
    } else {
        ""
    };
    if report.suppressed > 0 {
        tail.push(format!("{} suppressed{scope}", report.suppressed));
    }
    if report.in_debt > 0 {
        tail.push(format!("{} in debt{scope}", report.in_debt));
    }
    tail.push(plural(report.modules as usize, "module") + scope);
    tail.extend(fixable(&report.violations));
    if errors == 0 && warnings == 0 {
        let checks: Vec<String> = report
            .categories
            .iter()
            .map(|category| style.paint(&format!("✓ {category}"), GREEN))
            .collect();
        let mut parts = vec![if checks.is_empty() {
            style.paint("✓ nothing to check", GREEN)
        } else {
            checks.join(" ")
        }];
        if hints > 0 {
            parts.push(hint_text);
        }
        parts.extend(tail);
        return parts.join(" · ");
    }
    let mark = if report.failed() {
        style.paint("✗", RED)
    } else {
        style.paint("!", YELLOW)
    };
    let mut counts = vec![
        plural(errors, "error"),
        plural(warnings, "warning"),
        hint_text,
    ];
    counts.extend(tail);
    format!("{mark} {}", counts.join(" · "))
}

fn fixable(violations: &[Finding]) -> Vec<String> {
    let count = |kind: &str| {
        violations
            .iter()
            .filter(|violation| violation.applicability() == Some(kind))
            .count()
    };
    let (safe, unsafe_, suggested) = (count("safe"), count("unsafe"), count("display"));
    let mut parts = Vec::new();
    if safe > 0 && unsafe_ > 0 {
        parts.push(format!(
            "{safe} fixable with --fix, {unsafe_} more with --unsafe-fixes"
        ));
    } else if safe > 0 {
        parts.push(format!("{safe} fixable with --fix"));
    } else if unsafe_ > 0 {
        parts.push(format!("{unsafe_} fixable with --fix --unsafe-fixes"));
    }
    if suggested > 0 {
        parts.push(format!(
            "{} with --diff --suggestions",
            plural(suggested, "suggestion")
        ));
    }
    parts
}
