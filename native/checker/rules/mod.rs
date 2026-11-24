//! Ported rules. Each keeps the code, messages and payload of its Python original.
mod aliases;
mod annotation_quotes;
mod assignments;
mod broad_excepts;
mod call_arguments;
mod call_order;
mod call_receivers;
mod callables;
mod comprehensions;
mod conditional_expressions;
mod constants;
mod constructors;
mod context_managers;
mod dataclass_options;
mod definition_order;
mod delegates;
pub mod dependencies;
mod discriminators;
mod domain_exceptions;
mod durations;
mod enum_styles;
mod exports;
pub(crate) use exports::explicit_exports;
mod frozen_collections;
mod generic_syntax;
mod headers;
mod interface_docstrings;
mod loop_collections;
mod narrowing_asserts;
mod none_defaults;
mod parallel_fields;
mod parameters;
mod private_definitions;
mod project_bases;
mod providers;
mod reexport_convention;
mod secret_settings;
mod stateful_dataclasses;
pub mod structure;
pub mod testing;
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

pub const PORTED: [&str; 89] = [
    "ARC001", "ARC002", "ARC003", "ARC004", "ARC005", "ARC006", "ARC007", "ARC008", "ARC009",
    "ARC010", "ARC011", "ARC012", "ARC013", "ARC014", "CPX001", "CPX002", "CPX003", "CPX004",
    "CPX005", "CPX006", "CPX007", "CPX008", "FWK001", "FWK002", "IDM001", "IDM002", "IDM003",
    "IDM004", "IDM005", "IDM006", "IDM007", "IDM008", "IMP001", "IMP002", "IMP003", "IMP004",
    "LAY001", "LAY002", "LAY003", "LAY004", "LAY005", "LAY006", "LAY007", "LAY008", "LAY009",
    "LAY010", "LAY011", "MOD001", "MOD002", "MOD003", "MOD004", "MOD005", "MOD006", "MOD007",
    "MOD008", "MOD009", "MOD010", "MOD011", "MOD012", "TST001", "TST002", "TST003", "TST004",
    "TST005", "TST006", "TST007", "TST008", "TST009", "TST010", "TST011", "TST012", "TYP001",
    "TYP002", "TYP003", "TYP004", "TYP005", "TYP006", "TYP007", "TYP008", "VIS001", "VIS002",
    "VIS003", "VIS004", "VIS005", "VIS006", "VIS007", "VIS008", "VIS009", "VIS010",
];

/// The project-wide facts rules read besides their source.
#[derive(Clone, Copy)]
pub struct Project<'a> {
    pub contracts: &'a Contracts<'a>,
    pub targets: &'a PythonTargets,
    pub roots: &'a [String],
}

/// The source a rule decides on, and the policy that applies to it.
pub struct RuleInput<'a> {
    pub rule: &'a CatalogRule,
    pub source: &'a SourceFile,
    pub parsed: &'a ParsedSource,
    pub contracts: &'a Contracts<'a>,
    pub targets: &'a PythonTargets,
    pub roots: &'a [String],
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
            line: Some(finding.line),
            column: Some(finding.column),
            end_line: finding.end_line.or(Some(finding.line)),
            end_column: finding.end_column,
            source_module: Some(self.source.module.clone()),
            target_module: None,
            import_chain: Vec::new(),
            feature: None,
            layer: None,
            edge: None,
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
        roots: project.roots,
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
        "IDM001" => assignments::check(&input),
        "IDM002" => type_dispatch::check(&input),
        "IDM003" => callables::check(&input),
        "IDM004" => aliases::check(&input),
        "IDM005" => interface_docstrings::check(&input),
        "IDM006" => broad_excepts::check(&input),
        "IDM007" => narrowing_asserts::check(&input),
        "IDM008" => loop_collections::check(&input),
        "LAY002" => headers::check_docstring(&input),
        "LAY004" => definition_order::check(&input),
        "LAY006" => call_order::check(&input),
        "LAY008" => constants::check(&input),
        "LAY010" => exports::check_reexports(&input),
        "LAY011" => headers::check_logger(&input),
        "MOD003" => discriminators::check(&input),
        "MOD004" => enum_styles::check(&input),
        "MOD005" => stateful_dataclasses::check(&input),
        "MOD006" => dataclass_options::check(&input),
        "MOD007" => project_bases::check(&input),
        "MOD008" => domain_exceptions::check(&input),
        "MOD010" => durations::check(&input),
        "MOD011" => frozen_collections::check(&input),
        "MOD012" => parallel_fields::check(&input),
        "TYP001" => headers::check_forbidden_future(&input),
        "TYP002" => headers::check_redundant_future(&input),
        "TYP003" => none_defaults::check(&input),
        "TYP004" => context_managers::check(&input),
        "TYP006" => annotation_quotes::check(&input),
        "TYP007" => tuples::check(&input),
        "TYP008" => generic_syntax::check(&input),
        "VIS003" => exports::check_dunder_all(&input),
        "VIS006" => delegates::check(&input),
        "VIS007" => private_definitions::check(&input),
        "VIS008" => reexport_convention::check(&input),
        _ => Vec::new(),
    }
}
