//! MET006: `meta/missing-tool-suppression-reason`.
use super::{Finding, RuleInput};
use crate::checker::analysis::comments::{Comments, is_reason, tool_suppressions};
use crate::checker::violation::Violation;
use ruff_text_size::TextSize;
use serde_json::json;

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let comments = Comments::scan(input.parsed);
    let mut found = Vec::new();
    for (line, offset, text) in comments.ordered() {
        let directives = tool_suppressions(text);
        if directives.is_empty() {
            continue;
        }
        if is_reason(text)
            || comments
                .block_above(line)
                .into_iter()
                .filter_map(|above| comments.text(above))
                .any(is_reason)
        {
            continue;
        }
        let (line, column) = input.place(TextSize::try_from(offset).unwrap_or_default());
        let mut labels: Vec<&str> = Vec::new();
        for label in &directives {
            if !labels.contains(&label.as_str()) {
                labels.push(label);
            }
        }
        let checkers = labels.iter().filter(|label| is_type_checker(label)).count();
        let (finding, repair) = if checkers == labels.len() {
            ("a type-checker finding", "Fix the typing error")
        } else if checkers == 0 {
            ("a linter finding", "Fix the reported problem")
        } else {
            ("findings", "Fix the reported problems")
        };
        let named = labels
            .iter()
            .map(|label| format!("`{label}`"))
            .collect::<Vec<_>>()
            .join(" and ");
        let verb = if labels.len() == 1 { "hides" } else { "hide" };
        found.push(input.violation(Finding {
            message: format!("{named} {verb} {finding} without saying why"),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({"directives": directives, "automatic_rewrite": false}),
            hint: format!(
                "{repair}, or add ` -- <reason>` to the comment so a reviewer can tell when it can be removed."
            ),
            fix: None,
        }));
    }
    found
}

fn is_type_checker(label: &str) -> bool {
    label.starts_with("type:") || label.starts_with("pyright:") || label.starts_with("mypy:")
}
