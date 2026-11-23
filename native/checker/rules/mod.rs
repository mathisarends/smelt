//! Ported rules. Each keeps the code, messages and payload of its Python original.
mod conditional_expressions;
mod parameters;

use super::contracts::Contracts;
use super::files::SourceFile;
use super::settings::CatalogRule;
use super::source::ParsedSource;
use super::violation::Violation;
use serde_json::Value;

pub const PORTED: [&str; 2] = ["CPX003", "CPX005"];

/// The source a rule decides on, and the policy that applies to it.
pub struct RuleInput<'a> {
    pub rule: &'a CatalogRule,
    pub source: &'a SourceFile,
    pub parsed: &'a ParsedSource,
    pub contracts: &'a Contracts<'a>,
}

/// The parts of a finding that differ between rules.
pub struct Finding {
    pub message: String,
    pub line: usize,
    pub column: usize,
    pub end_line: Option<usize>,
    pub end_column: Option<usize>,
    pub expected: Value,
    pub hint: String,
    pub fix: Option<Value>,
}

impl RuleInput<'_> {
    pub fn violation(&self, finding: Finding) -> Violation {
        Violation {
            code: self.rule.code.clone(),
            rule: self.rule.name.clone(),
            category: self.rule.category.clone(),
            severity: self.rule.default_severity.clone(),
            message: finding.message,
            path: self.source.path.clone(),
            line: finding.line,
            column: finding.column,
            end_line: finding.end_line.or(Some(finding.line)),
            end_column: finding.end_column,
            source_module: Some(self.source.module.clone()),
            expected: finding.expected,
            hint: Some(finding.hint),
            fix: finding.fix,
        }
    }
}

pub fn check(
    rule: &CatalogRule,
    source: &SourceFile,
    parsed: &ParsedSource,
    contracts: &Contracts<'_>,
) -> Vec<Violation> {
    let input = RuleInput {
        rule,
        source,
        parsed,
        contracts,
    };
    match rule.code.as_str() {
        "CPX003" => parameters::check(&input),
        "CPX005" => conditional_expressions::check(&input),
        _ => Vec::new(),
    }
}
