//! Rules over the module inventory and its place in the architecture.
mod class_names;
mod coverage;
mod duplicate_functions;
mod enum_values;
mod file_naming;
mod import_style;
mod layout;
mod literal_types;
mod members;
mod module_symbols;
mod naming;
mod navigation;
pub mod package_imports;
mod private_access;
mod required_modules;
mod test_location;
mod untested;

use crate::checker::analysis::architecture::ArchitectureModel;
use crate::checker::analysis::coverage::Coverage;
use crate::checker::analysis::members::MemberUsageIndex;
use crate::checker::analysis::namespace::project_namespaces;
use crate::checker::analysis::test_syntax::{self, TestModule};
use crate::checker::config::{FileNaming, Project, TestPolicy};
use crate::checker::contracts::Contracts;
use crate::checker::files::{FileIndex, SourceFile};
use crate::checker::patterns::PathPattern;
use crate::checker::settings::CatalogRule;
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use serde_json::Value;
use std::sync::OnceLock;

pub const STRUCTURE_RULES: [&str; 25] = [
    "ARC011", "ARC012", "ARC013", "ARC014", "CPX001", "CPX004", "IMP003", "IMP004", "LAY001",
    "LAY003", "LAY005", "LAY007", "LAY009", "MOD001", "MOD002", "MOD009", "TST001", "TST012",
    "TYP005", "VIS001", "VIS002", "VIS004", "VIS005", "VIS009", "VIS010",
];

/// The structure rules that read discovered tests.
pub const TEST_READING_RULES: [&str; 8] = [
    "IMP004", "LAY005", "TST001", "VIS001", "VIS002", "VIS004", "VIS009", "VIS010",
];

/// The project-wide facts the structure rules read.
pub struct StructureContext<'a> {
    pub files: &'a FileIndex,
    pub model: &'a ArchitectureModel<'a>,
    pub contracts: &'a Contracts<'a>,
    pub sources: &'a [(&'a SourceFile, &'a ParsedSource)],
    /// Every discovered test, including those excluded from test-quality rules.
    pub tests: &'a [(&'a SourceFile, &'a ParsedSource)],
    pub test_roots: &'a [String],
    pub forbidden_names: &'a [String],
    pub file_naming: &'a FileNaming,
    pub project: &'a Project,
    pub test_policy: &'a TestPolicy,
    /// Whether `architecture/uncovered-source` guards the whole run.
    pub strict_coverage: bool,
    /// The strict coverage of the run, which its report reads as well.
    pub coverage: &'a OnceLock<Coverage>,
    /// Tests the test-quality rules exempt; they still count as use evidence.
    pub quality_exclude: &'a [PathPattern],
    pub member_usage: OnceLock<MemberUsageIndex<'a>>,
    pub test_modules: OnceLock<Vec<TestModule<'a>>>,
}

impl<'a> StructureContext<'a> {
    /// Built once, for the first rule that asks.
    fn member_usage(&self) -> &MemberUsageIndex<'a> {
        self.member_usage.get_or_init(|| {
            let namespaces = project_namespaces(self.sources, &self.files.packages);
            MemberUsageIndex::new(self.sources, self.tests, namespaces)
        })
    }

    /// The analyzed tests outside `tests.quality_exclude`.
    fn test_modules(&self) -> &[TestModule<'a>] {
        self.test_modules.get_or_init(|| {
            let tests: Vec<_> = self
                .tests
                .iter()
                .copied()
                .filter(|(test, _)| {
                    !self
                        .quality_exclude
                        .iter()
                        .any(|item| item.matches(&test.path))
                })
                .collect();
            test_syntax::analyze(self.sources, &tests)
        })
    }
}

/// The parts of a structure finding that differ between rules.
#[derive(Default)]
struct StructureFinding {
    message: String,
    path: String,
    /// The 1-based line and column of a finding inside a file.
    position: Option<(usize, usize)>,
    end_line: Option<usize>,
    end_column: Option<usize>,
    /// A finding on a line that reports no column.
    column_less: bool,
    source_module: Option<String>,
    target_module: Option<String>,
    feature: Option<String>,
    layer: Option<String>,
    expected: Value,
    hint: String,
    fix: Option<Value>,
}

/// Every finding of `rules` at its resolved severity.
pub fn findings<'r>(
    rules: &[&'r CatalogRule],
    context: &StructureContext<'_>,
    severity: impl Fn(&'r CatalogRule, &str, &str) -> Option<&'r str>,
) -> Result<Vec<Violation>, String> {
    let mut found = Vec::new();
    for rule in rules {
        let raised = match rule.code.as_str() {
            "ARC011" => layout::check_unknown_layers(context),
            "ARC012" => layout::check_unclassified(context),
            "ARC013" => coverage::check(context)?,
            "ARC014" => required_modules::check(context),
            "CPX001" => navigation::check(context),
            "CPX004" => duplicate_functions::check(context),
            "IMP003" => import_style::check(context),
            "IMP004" => package_imports::check(context),
            "LAY001" => naming::check_forbidden_names(context),
            "LAY003" => naming::check_private_modules(context),
            "LAY005" => members::check_static_helpers(context),
            "LAY007" => file_naming::check(context),
            "LAY009" => naming::check_redundant_names(context),
            "MOD001" => enum_values::check(context),
            "MOD002" => naming::check_base_affixes(context),
            "MOD009" => class_names::check(context),
            "TST001" => test_location::check(context),
            "TST012" => untested::check(context),
            "TYP005" => literal_types::check(context),
            "VIS001" => members::check_internal_members(context),
            "VIS002" => module_symbols::check(context),
            "VIS004" => members::check_internal_attributes(context),
            "VIS005" => private_access::check(context),
            "VIS009" => members::check_test_only_functions(context),
            "VIS010" => members::check_test_only_attributes(context),
            _ => Vec::new(),
        };
        for finding in raised {
            let module = finding.source_module.as_deref().unwrap_or_default();
            if let Some(level) = severity(rule, module, &finding.path) {
                let mut violation = violation(rule, finding);
                level.clone_into(&mut violation.severity);
                found.push(violation);
            }
        }
    }
    Ok(found)
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
        column: finding
            .position
            .filter(|_| !finding.column_less)
            .map(|(_, column)| column),
        end_line: finding.end_line.or(line.filter(|_| !finding.column_less)),
        end_column: finding.end_column,
        source_module: finding.source_module,
        target_module: finding.target_module,
        import_chain: Vec::new(),
        feature: finding.feature,
        layer: finding.layer,
        edge: None,
        expected: finding.expected,
        hint: Some(finding.hint),
        fix: finding.fix,
    }
}

fn last_segment(name: &str) -> &str {
    name.rsplit('.').next().unwrap_or(name)
}
