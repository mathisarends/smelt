//! TST002 to TST011: test-quality rules over the analyzed test modules.
use crate::checker::analysis::dump::dump_expr;
use crate::checker::analysis::test_syntax::{
    TestModule, call_only_async_mocks, helper_outcome_review, independent_case_loops,
    unbounded_waits, vacuous_outcomes,
};
use crate::checker::config::TestPolicy;
use crate::checker::patterns::PathPattern;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::Violation;
use ruff_python_ast::{Alias, Expr, Stmt};
use ruff_text_size::{Ranged, TextSize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

pub const TEST_RULES: [&str; 10] = [
    "TST002", "TST003", "TST004", "TST005", "TST006", "TST007", "TST008", "TST009", "TST010",
    "TST011",
];

const GENERATORS: [&str; 19] = [
    "uuid.uuid1",
    "uuid.uuid4",
    "os.urandom",
    "secrets.choice",
    "secrets.randbelow",
    "secrets.randbits",
    "secrets.token_bytes",
    "secrets.token_hex",
    "secrets.token_urlsafe",
    "random.random",
    "random.randint",
    "random.randrange",
    "random.choice",
    "random.choices",
    "random.sample",
    "random.shuffle",
    "random.uniform",
    "random.getrandbits",
    "random.randbytes",
];

/// The project facts test rules read.
pub struct TestProject<'a> {
    pub policy: &'a TestPolicy,
    pub known: &'a HashSet<&'a str>,
    pub forbidden_mocks: &'a [PathPattern],
}

struct Report<'r, 'a> {
    rule: &'r CatalogRule,
    module: &'r TestModule<'a>,
}

struct TestFinding {
    message: String,
    line: usize,
    column: Option<usize>,
    target_module: Option<String>,
    expected: Value,
    hint: String,
}

impl Report<'_, '_> {
    fn violation(&self, finding: TestFinding) -> Violation {
        Violation {
            code: self.rule.code.clone(),
            rule: self.rule.name.clone(),
            category: self.rule.category.clone(),
            severity: self.rule.default_severity.clone(),
            message: finding.message,
            path: self.module.path.to_owned(),
            line: finding.line,
            column: finding.column,
            end_line: finding.column.map(|_| finding.line),
            end_column: None,
            source_module: None,
            target_module: finding.target_module,
            expected: finding.expected,
            hint: Some(finding.hint),
            fix: None,
        }
    }

    fn at_line(&self, message: String, line: usize, expected: Value, hint: &str) -> Violation {
        self.violation(TestFinding {
            message,
            line,
            column: None,
            target_module: None,
            expected,
            hint: hint.to_owned(),
        })
    }

    fn at_place(
        &self,
        message: String,
        offset: TextSize,
        expected: Value,
        hint: &str,
    ) -> Violation {
        let (line, column) = self.module.place(offset);
        self.violation(TestFinding {
            message,
            line,
            column: Some(column),
            target_module: None,
            expected,
            hint: hint.to_owned(),
        })
    }
}

pub fn check(
    rule: &CatalogRule,
    module: &TestModule<'_>,
    project: &TestProject<'_>,
) -> Vec<Violation> {
    let report = Report { rule, module };
    match rule.code.as_str() {
        "TST002" => private_access(&report, project),
        "TST003" => forbidden_mocks(&report, project),
        "TST004" => string_patch_targets(&report),
        "TST005" => excess_patches(&report, project),
        "TST006" => missing_outcomes(&report, project),
        "TST007" => vacuous_assertions(&report, project),
        "TST008" => unbounded(&report, project),
        "TST009" => case_loops(&report),
        "TST010" => missing_awaits(&report),
        "TST011" => nondeterministic(&report),
        _ => Vec::new(),
    }
}

fn is_private(name: &str) -> bool {
    name.starts_with('_') && !(name.starts_with("__") && name.ends_with("__"))
}

fn aliases(statement: &Stmt) -> &[Alias] {
    match statement {
        Stmt::Import(node) => &node.names,
        Stmt::ImportFrom(node) => &node.names,
        _ => &[],
    }
}

fn private_access(report: &Report<'_, '_>, project: &TestProject<'_>) -> Vec<Violation> {
    let module = report.module;
    let mut accesses: Vec<(TextSize, Option<&Stmt>, &str, &str, &str)> = Vec::new();
    for (node, qualified) in &module.imports {
        accesses.push((node.start(), Some(node), qualified, "import", "module"));
    }
    for (node, qualified) in &module.attributes {
        accesses.push((node.start(), None, qualified, "import", "module"));
    }
    for item in &module.instance_attributes {
        accesses.push((
            item.node.start(),
            None,
            &item.qualified,
            item.provenance,
            "instance",
        ));
    }
    let mut seen: HashSet<(usize, usize, String)> = HashSet::new();
    let mut found = Vec::new();
    for (start, statement, qualified, provenance, access) in accesses {
        let parts: Vec<&str> = qualified.split('.').collect();
        if access == "instance" && !parts.last().is_some_and(|name| is_private(name)) {
            // Importing a private class is one decision, not another finding for
            // each public operation on that instance.
            continue;
        }
        if !(1..=parts.len()).any(|end| project.known.contains(parts[..end].join(".").as_str())) {
            continue;
        }
        let Some(private) = parts.iter().position(|name| is_private(name)) else {
            continue;
        };
        let target = if access == "instance" {
            qualified.to_owned()
        } else {
            parts[..=private].join(".")
        };
        let location = statement
            .and_then(|node| {
                aliases(node).iter().find(|alias| {
                    let name = alias.name.as_str();
                    qualified == name
                        || qualified
                            .strip_suffix(name)
                            .is_some_and(|rest| rest.ends_with('.'))
                })
            })
            .map_or(start, Ranged::start);
        let (line, column) = module.place(location);
        if !seen.insert((line, column, target.clone())) {
            continue;
        }
        found.push(
            report.violation(TestFinding {
                message: format!("test accesses private production name {target}"),
                line,
                column: Some(column),
                target_module: Some(target.clone()),
                expected: json!({
                    "private_target": target,
                    "access": access,
                    "provenance": provenance,
                }),
                hint: "Test the supported public behavior instead of an internal helper or module."
                    .to_owned(),
            }),
        );
    }
    found
}

fn forbidden_mocks(report: &Report<'_, '_>, project: &TestProject<'_>) -> Vec<Violation> {
    let module = report.module;
    if !project
        .forbidden_mocks
        .iter()
        .any(|pattern| pattern.matches(module.path))
    {
        return Vec::new();
    }
    module
        .calls
        .iter()
        .filter(|call| call.is_mock())
        .map(|call| {
            report.at_place(
                format!(
                    "mock operation {} is forbidden in this test path",
                    call.qualified
                ),
                call.node.start(),
                json!({
                    "forbidden_mock_paths": project.policy.forbidden_mock_paths,
                    "operation": call.qualified,
                    "family": call.family(),
                    "provenance": call.provenance,
                }),
                "Exercise the domain with real values and an injected fake instead of a mock.",
            )
        })
        .collect()
}

fn string_patch_targets(report: &Report<'_, '_>) -> Vec<Violation> {
    report
        .module
        .calls
        .iter()
        .filter(|call| call.has_string_target())
        .map(|call| {
            report.at_place(
                "patch uses a literal string target".to_owned(),
                call.node.start(),
                json!({
                    "operation": call.qualified,
                    "family": call.family(),
                    "provenance": call.provenance,
                }),
                "Inject the collaborator, or review deliberate object-based boundary patching.",
            )
        })
        .collect()
}

fn excess_patches(report: &Report<'_, '_>, project: &TestProject<'_>) -> Vec<Violation> {
    let Some(maximum) = project.policy.max_patches_per_test else {
        return Vec::new();
    };
    let module = report.module;
    let source = module.parsed.text.text.as_str();
    let mut found = Vec::new();
    for test in &module.tests {
        let patches: Vec<_> = test.calls.iter().filter(|call| call.is_patch()).collect();
        if u64::try_from(patches.len()).unwrap_or(u64::MAX) <= maximum {
            continue;
        }
        let mut counts: HashMap<String, usize> = HashMap::new();
        let mut edges = Vec::with_capacity(patches.len());
        for call in &patches {
            let shape = dump_expr(source, call.node);
            let count = counts.entry(shape.clone()).or_insert(0);
            edges.push(format!("{}:{shape}#{count}", test.name));
            *count += 1;
        }
        let listed: Vec<Value> = patches
            .iter()
            .map(|call| {
                json!({
                    "line": module.line(call.node.start()),
                    "operation": call.qualified,
                    "family": call.family(),
                    "provenance": call.provenance,
                })
            })
            .collect();
        found.push(report.at_line(
            format!(
                "{} has {} patch calls; maximum is {maximum}",
                test.name,
                patches.len()
            ),
            module.function_line(test.node),
            json!({
                "test": test.name,
                "patch_edges": edges,
                "max_patches_per_test": maximum,
                "patches": listed,
            }),
            "Exercise a smaller behavior or inject a fake instead of replacing internal collaborators.",
        ));
    }
    found
}

fn missing_outcomes(report: &Report<'_, '_>, project: &TestProject<'_>) -> Vec<Violation> {
    let module = report.module;
    let helpers = &project.policy.assertion_helpers;
    let mut found = Vec::new();
    for test in &module.tests {
        if test.has_assertion(module, helpers) {
            continue;
        }
        let children: Vec<Value> = test
            .calls
            .iter()
            .filter(|call| call.qualified.starts_with("subprocess."))
            .map(|call| {
                json!({
                    "line": module.line(call.node.start()),
                    "operation": call.qualified,
                    "child_assertions": "not inferred",
                })
            })
            .collect();
        let mut hint = "Check an observable result, state, expected exception/warning, or a configured assertion helper. Only one directly executed, eligible same-file helper is followed; imported bodies and further helper calls are not analyzed. helper_review explains missing evidence where known.".to_owned();
        if !children.is_empty() {
            hint.push_str(" Child assertions were not inferred: only checked sys.executable -c calls with statically known script text and direct assertions are recognized. Assert the parent-visible outcome for dynamic scripts or files.");
        }
        found.push(report.at_line(
            format!("{} has no recognized outcome assertion", test.name),
            module.function_line(test.node),
            json!({
                "test": test.name,
                "subprocess_review": children,
                "assertion_helpers": helpers,
                "helper_review": helper_outcome_review(test, module, helpers),
                "analysis_limits": {
                    "helper_hops": 1,
                    "same_file_only": true,
                    "imported_bodies": "not analyzed",
                },
            }),
            &hint,
        ));
    }
    found
}

fn vacuous_assertions(report: &Report<'_, '_>, project: &TestProject<'_>) -> Vec<Violation> {
    let module = report.module;
    let mut found = Vec::new();
    for test in &module.tests {
        let risks = vacuous_outcomes(test, module, &project.policy.assertion_helpers);
        if risks.is_empty() {
            continue;
        }
        let evidence: Vec<Value> = risks
            .iter()
            .map(|risk| json!({"line": module.line(risk.start()), "kind": risk.kind()}))
            .collect();
        found.push(report.at_line(
            format!(
                "{} only checks possibly empty data; its assertions can pass or be skipped without checking an item",
                test.name
            ),
            module.function_line(test.node),
            json!({"test": test.name, "evidence": evidence}),
            "Compare complete output or verify expected presence before iterating; review deliberate empty-result properties.",
        ));
    }
    found
}

fn unbounded(report: &Report<'_, '_>, project: &TestProject<'_>) -> Vec<Violation> {
    let module = report.module;
    let mut found = Vec::new();
    for test in &module.tests {
        for wait in unbounded_waits(test, module, &project.policy.timeout_helpers) {
            let (line, column) = module.place(wait.node.start());
            let mut expected = json!({
                "test": test.name,
                "kind": "unbounded-wait",
                "operation": wait.qualified,
                "provenance": wait.provenance,
            });
            if let Some(helper) = &wait.helper {
                expected["operation_location"] = json!({"line": line, "column": column});
                expected["helper"] = json!({
                    "name": helper,
                    "call_line": wait.helper_call_line,
                    "definition_line": wait.helper_line,
                    "call_lines": wait.helper_call_lines,
                });
            }
            found.push(report.at_line(
                format!(
                    "{} has no recognized deadline for {}",
                    test.name, wait.qualified
                ),
                line,
                expected,
                "Bound this operation locally or review the independently enforced test deadline.",
            ));
        }
        for node in &test.clock_replacements {
            found.push(report.at_line(
                format!("{} replaces the asyncio scheduler/deadline clock", test.name),
                module.line(node.start()),
                json!({
                    "test": test.name,
                    "kind": "scheduler-clock",
                    "operation": "asyncio.AbstractEventLoop.time",
                }),
                "Review an independent real-time guard; deadlines using this clock can also freeze.",
            ));
        }
    }
    found
}

fn case_loops(report: &Report<'_, '_>) -> Vec<Violation> {
    let module = report.module;
    let mut found = Vec::new();
    for test in &module.tests {
        for candidate in independent_case_loops(test) {
            let cases = match candidate.iter.as_ref() {
                Expr::List(item) => item.elts.len(),
                Expr::Tuple(item) => item.elts.len(),
                _ => 0,
            };
            found.push(report.at_line(
                format!(
                    "{} repeats candidate independent cases in a literal loop",
                    test.name
                ),
                module.line(candidate.start()),
                json!({"test": test.name, "cases": cases}),
                "Review parametrization with descriptive IDs; preserve stateful lifecycles.",
            ));
        }
    }
    found
}

fn missing_awaits(report: &Report<'_, '_>) -> Vec<Violation> {
    let module = report.module;
    let mut found = Vec::new();
    for test in &module.tests {
        for observation in call_only_async_mocks(test, module.parsed) {
            found.push(report.at_line(
                format!(
                    "{} checks calls to {} without await evidence",
                    test.name, observation.receiver
                ),
                module.line(observation.node.start()),
                json!({
                    "test": test.name,
                    "receiver": observation.receiver,
                    "async_callable": observation.async_callable,
                    "provenance": observation.provenance,
                }),
                "Check this operation was awaited when execution matters; review deliberate awaitable creation/scheduling.",
            ));
        }
    }
    found
}

fn nondeterministic(report: &Report<'_, '_>) -> Vec<Violation> {
    let module = report.module;
    let mut calls: BTreeMap<usize, BTreeSet<&str>> = BTreeMap::new();
    for call in &module.calls {
        if GENERATORS.contains(&call.qualified.as_str()) {
            calls
                .entry(module.line(call.node.start()))
                .or_default()
                .insert(call.qualified.as_str());
        }
    }
    calls
        .into_iter()
        .map(|(line, generators)| {
            report.at_line(
                "review random test data; prefer fixed inputs or a seeded local generator"
                    .to_owned(),
                line,
                json!({"generators": generators, "automatic_rewrite": false}),
                "Use fixed IDs/values for incidental setup; keep intentional randomness tests with a specific reason.",
            )
        })
        .collect()
}
