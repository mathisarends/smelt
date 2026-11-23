//! Rules over the module inventory and its place in the architecture.
mod class_names;
mod file_naming;
mod layout;
mod naming;
mod required_modules;

use crate::checker::analysis::architecture::ArchitectureModel;
use crate::checker::config::FileNaming;
use crate::checker::contracts::Contracts;
use crate::checker::files::{FileIndex, SourceFile};
use crate::checker::settings::CatalogRule;
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use serde_json::Value;

pub const STRUCTURE_RULES: [&str; 9] = [
    "ARC011", "ARC012", "ARC014", "LAY001", "LAY003", "LAY007", "LAY009", "MOD002", "MOD009",
];

/// The project-wide facts the structure rules read.
pub struct StructureContext<'a> {
    pub files: &'a FileIndex,
    pub model: &'a ArchitectureModel<'a>,
    pub contracts: &'a Contracts<'a>,
    pub sources: &'a [(&'a SourceFile, &'a ParsedSource)],
    pub forbidden_names: &'a [String],
    pub file_naming: &'a FileNaming,
}

/// The parts of a structure finding that differ between rules.
#[derive(Default)]
struct StructureFinding {
    message: String,
    path: String,
    /// The 1-based line and column of a finding inside a file.
    position: Option<(usize, usize)>,
    source_module: Option<String>,
    target_module: Option<String>,
    feature: Option<String>,
    layer: Option<String>,
    expected: Value,
    hint: String,
}

/// Every finding of `rules` at its resolved severity.
pub fn findings<'r>(
    rules: &[&'r CatalogRule],
    context: &StructureContext<'_>,
    severity: impl Fn(&'r CatalogRule, &str) -> Option<&'r str>,
) -> Vec<Violation> {
    let mut found = Vec::new();
    for rule in rules {
        let raised = match rule.code.as_str() {
            "ARC011" => layout::check_unknown_layers(context),
            "ARC012" => layout::check_unclassified(context),
            "ARC014" => required_modules::check(context),
            "LAY001" => naming::check_forbidden_names(context),
            "LAY003" => naming::check_private_modules(context),
            "LAY007" => file_naming::check(context),
            "LAY009" => naming::check_redundant_names(context),
            "MOD002" => naming::check_base_affixes(context),
            "MOD009" => class_names::check(context),
            _ => Vec::new(),
        };
        for finding in raised {
            let module = finding.source_module.as_deref().unwrap_or_default();
            if let Some(level) = severity(rule, module) {
                let mut violation = violation(rule, finding);
                level.clone_into(&mut violation.severity);
                found.push(violation);
            }
        }
    }
    found
}

fn violation(rule: &CatalogRule, finding: StructureFinding) -> Violation {
    let line = finding.position.map(|(line, _)| line);
    Violation {
        code: rule.code.clone(),
        rule: rule.name.clone(),
        category: rule.category.clone(),
        severity: rule.default_severity.clone(),
        message: finding.message,
        path: finding.path,
        line,
        column: finding.position.map(|(_, column)| column),
        end_line: line,
        end_column: None,
        source_module: finding.source_module,
        target_module: finding.target_module,
        import_chain: Vec::new(),
        feature: finding.feature,
        layer: finding.layer,
        edge: None,
        expected: finding.expected,
        hint: Some(finding.hint),
        fix: None,
    }
}

fn last_segment(name: &str) -> &str {
    name.rsplit('.').next().unwrap_or(name)
}
