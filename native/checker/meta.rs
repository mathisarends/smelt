//! MET001 and MET002: inline suppressions that are unused, unknown or unexplained.
use super::analysis::edits::fix;
use super::config::errors::did_you_mean;
use super::renames::{replacement_selectors, retired_hint};
use super::settings::CatalogRule;
use super::source::SourceText;
use super::suppressions::{Suppression, directive_edit, identifier, is_rule_name};
use super::violation::Violation;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

/// The meta rules evaluated after filtering rather than by a rule check.
pub const META_RULES: [&str; 5] = ["MET001", "MET002", "MET003", "MET004", "MET005"];

/// What MET001 and MET002 need to judge the suppressions of a run.
pub struct SuppressionAudit<'a> {
    /// MET001 and its severity, when active.
    pub unused: Option<(&'a CatalogRule, &'a str)>,
    /// MET002 and its severity, when active and `suppressions.require_reason` holds.
    pub reason: Option<(&'a CatalogRule, &'a str)>,
    /// Every code of the catalog.
    pub known: Vec<&'a str>,
    /// The rules a full run with this configuration executes.
    pub enabled: HashSet<&'a str>,
    /// The rules this run executed.
    pub checked: HashSet<&'a str>,
    pub names: &'a HashMap<&'a str, &'a str>,
}

impl SuppressionAudit<'_> {
    pub fn is_active(&self) -> bool {
        self.unused.is_some() || self.reason.is_some()
    }

    /// The meta findings of one file's suppressions and the codes each one hid.
    pub fn findings(
        &self,
        path: &str,
        text: &SourceText,
        suppressions: &[Suppression],
        usage: &[Vec<String>],
    ) -> Vec<Violation> {
        let mut found = Vec::new();
        for (suppression, used) in suppressions.iter().zip(usage) {
            if let Some((rule, severity)) = self.reason
                && suppression.reason.is_none()
            {
                found.push(violation(
                    rule,
                    severity,
                    path,
                    suppression,
                    "suppression without a reason".to_owned(),
                    Value::Null,
                    "Append ` -- <why this exception is acceptable>` to the comment.".to_owned(),
                    None,
                ));
            }
            let Some((rule, severity)) = self.unused else {
                continue;
            };
            let unknown: Vec<&str> = suppression
                .codes
                .iter()
                .filter(|prefix| {
                    !self
                        .known
                        .iter()
                        .any(|code| code.starts_with(prefix.as_str()))
                })
                .map(|prefix| suppression.label(prefix))
                .collect();
            if !unknown.is_empty() {
                found.push(violation(
                    rule,
                    severity,
                    path,
                    suppression,
                    format!("unknown suppression identifiers ({})", unknown.join(", ")),
                    json!({"unknown_identifiers": unknown}),
                    self.unknown_hint(&unknown),
                    unknown_fix(text, suppression, &unknown),
                ));
            }
            let Some(unused) = self.unused_codes(suppression, used) else {
                continue;
            };
            let label = if unused.is_empty() {
                "all codes".to_owned()
            } else {
                unused
                    .iter()
                    .map(|code| suppression.label(code))
                    .collect::<Vec<_>>()
                    .join(", ")
            };
            found.push(violation(
                rule,
                severity,
                path,
                suppression,
                format!("unused suppression ({label})"),
                Value::Null,
                "Remove the suppression; nothing on this line needs it.".to_owned(),
                unused_fix(text, suppression, &unused),
            ));
        }
        found
    }

    /// Codes to remove, empty for the whole comment, `None` if the suppression is fine.
    ///
    /// A code only counts as unused when every enabled rule it refers to ran; a
    /// narrowed run cannot judge the others.
    fn unused_codes<'s>(
        &self,
        suppression: &'s Suppression,
        used: &[String],
    ) -> Option<Vec<&'s str>> {
        if suppression.codes.is_empty() {
            let all_checked = self.enabled.is_subset(&self.checked);
            return (used.is_empty() && all_checked).then(Vec::new);
        }
        let evaluated: Vec<&str> = suppression
            .unused_codes(used)
            .into_iter()
            .filter(|prefix| {
                let matching: Vec<&str> = self
                    .known
                    .iter()
                    .copied()
                    .filter(|code| code.starts_with(prefix))
                    .collect();
                !matching.is_empty()
                    && matching
                        .iter()
                        .filter(|code| self.enabled.contains(*code))
                        .all(|code| self.checked.contains(code))
            })
            .collect();
        if evaluated.is_empty() {
            return None;
        }
        if evaluated.len() == suppression.codes.len() {
            return Some(Vec::new());
        }
        Some(evaluated)
    }

    /// Point retired or misspelled identifiers to their replacement.
    fn unknown_hint(&self, unknown: &[&str]) -> String {
        let names: Vec<&str> = self.names.keys().copied().collect();
        let suggestions: Vec<String> = unknown
            .iter()
            .map(|identifier| {
                let retired = retired_hint(identifier, self.names);
                if !retired.is_empty() {
                    retired
                } else if is_rule_name(identifier) {
                    did_you_mean(identifier, &names)
                } else {
                    String::new()
                }
            })
            .collect();
        if suggestions.iter().all(String::is_empty) {
            return "Use a rule name or a rule code; `smelt rules` lists both.".to_owned();
        }
        unknown
            .iter()
            .zip(&suggestions)
            .filter(|(_, suggestion)| !suggestion.is_empty())
            .map(|(identifier, suggestion)| format!("{identifier}{suggestion}."))
            .collect::<Vec<_>>()
            .join(" ")
    }
}

#[allow(clippy::too_many_arguments)]
fn violation(
    rule: &CatalogRule,
    severity: &str,
    path: &str,
    suppression: &Suppression,
    message: String,
    expected: Value,
    hint: String,
    fix: Option<Value>,
) -> Violation {
    Violation {
        code: rule.code.clone(),
        rule: rule.name.clone(),
        category: rule.category.clone(),
        severity: severity.to_owned(),
        message,
        path: path.to_owned(),
        line: Some(suppression.line),
        column: Some(suppression.column + 1),
        end_line: Some(suppression.line),
        end_column: None,
        source_module: None,
        target_module: None,
        import_chain: Vec::new(),
        feature: None,
        layer: None,
        edge: None,
        expected,
        hint: Some(hint),
        fix,
    }
}

/// Drop the unused identifiers, or the whole directive when `unused` is empty.
///
/// Safe: the removed identifiers suppress nothing in a run that evaluated them.
fn unused_fix(text: &SourceText, suppression: &Suppression, unused: &[&str]) -> Option<Value> {
    let drop: Vec<(String, Vec<String>)> = unused
        .iter()
        .map(|code| (identifier(suppression.label(code)), Vec::new()))
        .collect();
    let edit = directive_edit(text, suppression, (!drop.is_empty()).then_some(&drop[..]))?;
    let description = if unused.is_empty() {
        "Remove the unused suppression"
    } else {
        "Remove the unused identifiers from the suppression"
    };
    Some(fix("safe", description, vec![edit]))
}

/// Replace retired identifiers with their successors and drop the others.
///
/// Replacing a retired name is safe. Dropping an identifier nothing knows is only
/// suggested: it is often a typo of the rule the comment means to suppress.
fn unknown_fix(text: &SourceText, suppression: &Suppression, unknown: &[&str]) -> Option<Value> {
    let drop: Vec<(String, Vec<String>)> = unknown
        .iter()
        .map(|label| {
            let replacements = replacement_selectors(label)
                .into_iter()
                .map(str::to_owned)
                .collect();
            (identifier(label), replacements)
        })
        .collect();
    let edit = directive_edit(text, suppression, Some(&drop))?;
    if drop
        .iter()
        .all(|(_, replacements)| !replacements.is_empty())
    {
        return Some(fix(
            "safe",
            "Replace retired identifiers with their current names",
            vec![edit],
        ));
    }
    Some(fix(
        "display",
        "Remove unknown identifiers that match no rule",
        vec![edit],
    ))
}
