//! LAY004: definitions out of public-first order within execution boundaries.
use super::RuleInput;
use crate::checker::analysis::edits::{fix, whole_line_edit};
use crate::checker::analysis::ordering::{self, FileOrder, OrderingConfig, rewrite_order};
use crate::checker::analysis::python_target::PythonTargets;
use crate::checker::contracts::Contracts;
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use serde_json::{Value, json};

const DEFAULT_CONTRACT: &str = "rules.layout/definition-order";

/// The definition order of one source under the ordering policy that governs it.
pub fn file_order<'a>(
    source: &SourceFile,
    parsed: &'a ParsedSource,
    contracts: &Contracts<'_>,
    targets: &PythonTargets,
) -> FileOrder<'a> {
    let origin = contracts.policy_for(&source.module, "ordering");
    let config = OrderingConfig::new(origin.as_ref().map(|origin| origin.value));
    let contract = origin.map_or(DEFAULT_CONTRACT, |origin| origin.contract);
    let target = targets.for_path(&source.absolute);
    ordering::analyze(parsed, config, contract.to_owned(), &target)
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let source = input.source;
    let order = file_order(source, input.parsed, input.contracts, input.targets);
    order
        .findings(input.parsed, &source.path)
        .into_iter()
        .map(|finding| {
            let mut expected = finding.expected;
            let mut repair = None;
            if expected["fixable"] == Value::Bool(true) {
                let key = expected["section"].as_str().unwrap_or_default();
                if let Some(section) = order.fixable_section(key) {
                    match rewrite_order(input.parsed, section) {
                        Ok(rewritten) => {
                            repair = Some(fix(
                                "safe",
                                "Move the section's definitions into the expected order",
                                vec![whole_line_edit(&input.parsed.text, &rewritten)],
                            ));
                        }
                        Err(reason) => expected["fix_reason"] = json!(reason),
                    }
                }
                expected["fixable"] = json!(repair.is_some());
            }
            let reason = expected["fix_reason"]
                .as_str()
                .unwrap_or("Use the expected order; bounded fixes require explicit --fix.");
            let hint = format!(
                "Keep execution boundaries and prerequisite definitions in place. {reason}"
            );
            Violation {
                code: input.rule.code.clone(),
                rule: input.rule.name.clone(),
                category: input.rule.category.clone(),
                severity: input.rule.default_severity.clone(),
                message: finding.message,
                path: source.path.clone(),
                line: Some(finding.line),
                column: None,
                end_line: None,
                end_column: None,
                source_module: Some(source.module.clone()),
                target_module: None,
                import_chain: Vec::new(),
                feature: None,
                layer: None,
                edge: None,
                expected,
                hint: Some(hint),
                fix: repair,
            }
        })
        .collect()
}
