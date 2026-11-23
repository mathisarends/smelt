//! Ported rules. Each keeps the code, messages and payload of its Python original.
mod aliases;
mod broad_excepts;
mod call_arguments;
mod call_receivers;
mod callables;
mod comprehensions;
mod conditional_expressions;
mod constructors;
mod delegates;
mod discriminators;
mod domain_exceptions;
mod durations;
mod exports;
mod frozen_collections;
mod interface_docstrings;
mod loop_collections;
mod narrowing_asserts;
mod parallel_fields;
mod parameters;
mod private_definitions;
mod project_bases;
mod providers;
mod secret_settings;
mod stateful_dataclasses;
mod tuples;
mod type_dispatch;

use super::analysis::python_target::PythonTargets;
use super::contracts::Contracts;
use super::files::SourceFile;
use super::settings::CatalogRule;
use super::source::ParsedSource;
use super::violation::Violation;
use ruff_text_size::TextSize;
use serde_json::Value;

pub const PORTED: [&str; 27] = [
    "CPX002", "CPX003", "CPX005", "CPX006", "CPX007", "CPX008", "FWK001", "FWK002", "IDM002",
    "IDM003", "IDM004", "IDM005", "IDM006", "IDM007", "IDM008", "LAY010", "MOD003", "MOD005",
    "MOD007", "MOD008", "MOD010", "MOD011", "MOD012", "TYP007", "VIS003", "VIS006", "VIS007",
];

/// The project-wide facts rules read besides their source.
#[derive(Clone, Copy)]
pub struct Project<'a> {
    pub contracts: &'a Contracts<'a>,
    pub targets: &'a PythonTargets,
}

/// The source a rule decides on, and the policy that applies to it.
pub struct RuleInput<'a> {
    pub rule: &'a CatalogRule,
    pub source: &'a SourceFile,
    pub parsed: &'a ParsedSource,
    pub contracts: &'a Contracts<'a>,
    pub targets: &'a PythonTargets,
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
    /// The 1-based line and 1-based column of an offset, as findings report them.
    pub fn place(&self, offset: TextSize) -> (usize, usize) {
        let (line, column) = self.parsed.text.position(usize::from(offset));
        (line, column + 1)
    }

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
    project: Project<'_>,
) -> Vec<Violation> {
    let input = RuleInput {
        rule,
        source,
        parsed,
        contracts: project.contracts,
        targets: project.targets,
    };
    match rule.code.as_str() {
        "CPX002" => call_arguments::check(&input),
        "CPX003" => parameters::check(&input),
        "CPX005" => conditional_expressions::check(&input),
        "CPX006" => comprehensions::check(&input),
        "CPX007" => constructors::check(&input),
        "CPX008" => call_receivers::check(&input),
        "FWK001" => secret_settings::check(&input),
        "FWK002" => providers::check(&input),
        "IDM002" => type_dispatch::check(&input),
        "IDM003" => callables::check(&input),
        "IDM004" => aliases::check(&input),
        "IDM005" => interface_docstrings::check(&input),
        "IDM006" => broad_excepts::check(&input),
        "IDM007" => narrowing_asserts::check(&input),
        "IDM008" => loop_collections::check(&input),
        "LAY010" => exports::check_reexports(&input),
        "MOD003" => discriminators::check(&input),
        "MOD005" => stateful_dataclasses::check(&input),
        "MOD007" => project_bases::check(&input),
        "MOD008" => domain_exceptions::check(&input),
        "MOD010" => durations::check(&input),
        "MOD011" => frozen_collections::check(&input),
        "MOD012" => parallel_fields::check(&input),
        "TYP007" => tuples::check(&input),
        "VIS003" => exports::check_dunder_all(&input),
        "VIS006" => delegates::check(&input),
        "VIS007" => private_definitions::check(&input),
        _ => Vec::new(),
    }
}
