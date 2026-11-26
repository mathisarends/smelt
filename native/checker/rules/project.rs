//! Project-declared call matchers share the parsed source and a single call visitor.
use super::{Finding, RuleInput};
use crate::checker::Analysis;
use crate::checker::analysis::bindings::{qualified_name, unshadowed_bindings};
use crate::checker::files::SourceFile;
use crate::checker::settings::CatalogRule;
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use ruff_python_ast::Expr;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_text_size::Ranged;
use serde::Deserialize;
use serde_json::json;
use std::collections::HashMap;

#[derive(Clone, Deserialize)]
pub struct Declaration {
    name: String,
    r#match: CallMatcher,
    message: String,
    guide: String,
}

#[derive(Clone, Deserialize)]
struct CallMatcher {
    target: String,
    layer: Option<String>,
}

impl From<&Declaration> for CatalogRule {
    fn from(declaration: &Declaration) -> Self {
        Self {
            code: declaration.name.clone(),
            name: declaration.name.clone(),
            category: "project".into(),
            default_severity: "warning".into(),
            enabled_by_default: false,
            scope: "source".into(),
            requires: vec!["syntax".into()],
            declaration: Some(declaration.clone()),
        }
    }
}

struct Target<'a> {
    input: RuleInput<'a>,
    declaration: &'a Declaration,
    severity: &'a str,
}

struct Calls<'a> {
    bindings: HashMap<String, String>,
    targets: HashMap<&'a str, Vec<Target<'a>>>,
    layer: Option<&'a str>,
    found: Vec<Violation>,
}

impl<'ast> Visitor<'ast> for Calls<'_> {
    fn visit_expr(&mut self, expr: &'ast Expr) {
        if let Expr::Call(call) = expr
            && let Some(name) = qualified_name(&call.func, &self.bindings)
            && let Some(targets) = self.targets.get(name.as_str())
        {
            for target in targets {
                let (line, column) = target.input.place(call.start());
                let (end_line, end_column) = target.input.place(call.end());
                let mut finding = target.input.violation(Finding {
                    message: target.declaration.message.clone(),
                    line,
                    column,
                    end_line: Some(end_line),
                    end_column: Some(end_column),
                    expected: json!({"call": name}),
                    hint: target.declaration.guide.clone(),
                    fix: None,
                });
                finding.severity = target.severity.to_owned();
                finding.layer = self.layer.map(str::to_owned);
                self.found.push(finding);
            }
        }
        visitor::walk_expr(self, expr);
    }
}

pub(in crate::checker) fn check(
    rules: &[&CatalogRule],
    source: &SourceFile,
    parsed: &ParsedSource,
    layer: Option<&str>,
    analysis: &Analysis<'_>,
) -> Vec<Violation> {
    let mut targets: HashMap<&str, Vec<Target<'_>>> = HashMap::new();
    for rule in rules {
        let Some(declaration) = &rule.declaration else {
            continue;
        };
        if declaration
            .r#match
            .layer
            .as_deref()
            .is_some_and(|wanted| Some(wanted) != layer)
        {
            continue;
        }
        let Some(severity) = analysis.settings.severity(
            rule,
            analysis.project.contracts,
            &source.module,
            &source.path,
            analysis.selected,
        ) else {
            continue;
        };
        let input = RuleInput {
            rule,
            source,
            parsed,
            contracts: analysis.project.contracts,
            targets: analysis.project.targets,
            roots: analysis.project.roots,
        };
        targets
            .entry(&declaration.r#match.target)
            .or_default()
            .push(Target {
                input,
                declaration,
                severity,
            });
    }
    if targets.is_empty() {
        return Vec::new();
    }
    let module = parsed.module();
    let mut calls = Calls {
        bindings: unshadowed_bindings(&parsed.text.text, module, &[]),
        targets,
        layer,
        found: Vec::new(),
    };
    calls.visit_body(&module.body);
    calls.found
}
