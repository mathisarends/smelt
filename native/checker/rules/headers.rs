//! LAY002, LAY011, TYP001 and TYP002: what opens a module.
use super::dataclass_options::BUILTIN_NAMES;
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{
    BindingCounts, Coverage, qualified_name, unshadowed_bindings,
};
use crate::checker::analysis::edits::{fix, guarded_imports, is_docstring, python_strip};
use crate::checker::analysis::python_target::PythonTarget;
use crate::checker::contracts::PolicyOrigin;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprName, ModModule, Operator, Parameter, Stmt};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::HashMap;

const GENERICS_VERSION: (u64, u64) = (3, 9);
const UNIONS_VERSION: (u64, u64) = (3, 10);
const BUILTIN_GENERICS: [&str; 6] = ["list", "dict", "tuple", "set", "frozenset", "type"];
const RUNTIME_NAMES: [&str; 3] = ["__annotations__", "get_type_hints", "TYPE_CHECKING"];
const LOGGER_HINT: &str = "Group module loggers after imports and before constants. Review initialization dependencies before moving them.";
const FUTURE_HINT: &str = "Follow the package convention and verify forward references, runtime annotation consumers and tests before removing the import.";

/// LAY011.
pub fn check_logger(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let source = input.parsed.text.text.as_str();
    let bindings = unshadowed_bindings(source, module, &[]);
    if !bindings
        .values()
        .any(|target| matches!(target.as_str(), "logging" | "logging.getLogger"))
    {
        return Vec::new();
    }
    let body = if docstring(module).is_some() {
        &module.body[1..]
    } else {
        module.body.as_slice()
    };
    let mut in_header = true;
    let mut pending = Vec::new();
    let mut misplaced = Vec::new();
    for statement in body {
        if in_header && guarded_imports(statement) {
            misplaced.append(&mut pending);
        } else if let Some(target) = logger_target(statement, &bindings) {
            if in_header {
                pending.push((statement, target));
            } else {
                misplaced.push((statement, target));
            }
        } else {
            in_header = false;
        }
    }
    misplaced
        .into_iter()
        .map(|(statement, target)| {
            let (line, column) = input.place(target.start());
            let (end_line, end_column) = input.place(target.end());
            input.violation(Finding {
                message: format!(
                    "place module logger {} immediately after imports",
                    target.id
                ),
                line,
                column,
                end_line: Some(end_line),
                end_column: Some(end_column),
                expected: json!({"logger": target.id.as_str(), "placement": "after-imports"}),
                hint: LOGGER_HINT.to_owned(),
                fix: logger_fix(input, statement, target.id.as_str(), &bindings),
            })
        })
        .collect()
}

/// LAY002.
pub fn check_docstring(input: &RuleInput<'_>) -> Vec<Violation> {
    let Some(contract) = input
        .contracts
        .policy_for(&input.source.module, "module_docstrings")
    else {
        return Vec::new();
    };
    let Some(mode @ ("forbid" | "require")) = contract.value.as_str() else {
        return Vec::new();
    };
    let module = input.parsed.module();
    let expected = json!({"contract": contract.contract, "module_docstrings": mode});
    let text = docstring(module);
    if mode == "forbid" && text.is_some() {
        let node = &module.body[0];
        let (line, column) = input.place(node.start());
        let (end_line, end_column) = input.place(node.end());
        return vec![input.violation(Finding {
            message: format!("{} forbids module docstrings", contract.label),
            line,
            column,
            end_line: Some(end_line),
            end_column: Some(end_column),
            expected,
            hint: "Remove or relocate the module docstring; keep useful class and function documentation.".to_owned(),
            fix: None,
        })];
    }
    if mode == "require" && text.is_none_or(|value| python_strip(value).is_empty()) {
        return vec![input.violation(Finding {
            message: format!("{} requires a non-empty module docstring", contract.label),
            line: 1,
            column: 1,
            end_line: None,
            end_column: None,
            expected,
            hint: "Add a module docstring before imports, or mark a justified file exception with # smelt: ignore-file[LAY002] -- reason.".to_owned(),
            fix: None,
        })];
    }
    Vec::new()
}

/// TYP001.
pub fn check_forbidden_future(input: &RuleInput<'_>) -> Vec<Violation> {
    future_findings(input, true)
}

/// TYP002.
pub fn check_redundant_future(input: &RuleInput<'_>) -> Vec<Violation> {
    future_findings(input, false)
}

fn future_findings(input: &RuleInput<'_>, forbid: bool) -> Vec<Violation> {
    let Some((contract, python, prohibited)) = future_policy(input) else {
        return Vec::new();
    };
    if prohibited != forbid {
        return Vec::new();
    }
    let module = input.parsed.module();
    let imports: Vec<&Stmt> = module
        .body
        .iter()
        .filter(|statement| {
            matches!(statement, Stmt::ImportFrom(node)
                if node.level == 0
                    && node.module.as_ref().is_some_and(|name| name.as_str() == "__future__")
                    && node.names.iter().any(|alias| alias.name.as_str() == "annotations"))
        })
        .collect();
    if imports.is_empty()
        || (!prohibited && has_postponement_evidence(input, module, python.minimum))
    {
        return Vec::new();
    }
    imports
        .into_iter()
        .map(|node| {
            let (line, column) = input.place(node.start());
            let (end_line, end_column) = input.place(node.end());
            input.violation(Finding {
                message: if prohibited {
                    format!("{} forbids the annotations future import", contract.label)
                } else {
                    "no static postponement need found; review the annotations future import"
                        .to_owned()
                },
                line,
                column,
                end_line: Some(end_line),
                end_column: Some(end_column),
                expected: json!({
                    "contract": contract.contract,
                    "future_annotations": contract.value,
                    "automatic_removal": false,
                    "python": python.to_json(),
                }),
                hint: FUTURE_HINT.to_owned(),
                fix: None,
            })
        })
        .collect()
}

/// The governing policy and target; `None` when the policy allows the import.
fn future_policy<'a>(input: &RuleInput<'a>) -> Option<(PolicyOrigin<'a>, PythonTarget, bool)> {
    let contract = input
        .contracts
        .policy_for(&input.source.module, "future_annotations")?;
    let mode = contract.value.as_str()?;
    if !matches!(mode, "forbid" | "when-needed") {
        return None;
    }
    let python = input.targets.for_path(&input.source.absolute);
    let prohibited = mode == "forbid" || python.deferred_annotations();
    Some((contract, python, prohibited))
}

/// `ast.get_docstring(tree, clean=False)`.
fn docstring(module: &ModModule) -> Option<&str> {
    match module.body.first()? {
        Stmt::Expr(node) => match node.value.as_ref() {
            Expr::StringLiteral(text) => Some(text.value.to_str()),
            _ => None,
        },
        _ => None,
    }
}

fn logger_target<'a>(
    statement: &'a Stmt,
    bindings: &HashMap<String, String>,
) -> Option<&'a ExprName> {
    let (target, value) = match statement {
        Stmt::Assign(node) => match node.targets.as_slice() {
            [Expr::Name(target)] => (target, node.value.as_ref()),
            _ => return None,
        },
        Stmt::AnnAssign(node) => (node.target.as_name_expr()?, node.value.as_deref()?),
        _ => return None,
    };
    let Expr::Call(call) = value else {
        return None;
    };
    let is_module_name =
        |item: &Expr| matches!(item, Expr::Name(name) if name.id.as_str() == "__name__");
    let named = match (&*call.arguments.args, &*call.arguments.keywords) {
        ([argument], []) => is_module_name(argument),
        ([], [keyword]) => {
            keyword
                .arg
                .as_ref()
                .is_some_and(|name| name.as_str() == "name")
                && is_module_name(&keyword.value)
        }
        _ => false,
    };
    (named && qualified_name(&call.func, bindings).as_deref() == Some("logging.getLogger"))
        .then_some(target)
}

/// Move the logger statement directly after the imports.
///
/// Safe: it only reads imports and `__name__`, and binding it earlier lets later
/// module code see it no later than before.
fn logger_fix(
    input: &RuleInput<'_>,
    statement: &Stmt,
    name: &str,
    bindings: &HashMap<String, String>,
) -> Option<Value> {
    let parsed = input.parsed;
    let text = &parsed.text;
    let module = parsed.module();
    if BindingCounts::new(module, Coverage::Full).get(name) != 1
        || !text.stands_alone(statement.range())
    {
        return None;
    }
    if let Stmt::AnnAssign(node) = statement {
        let mut reads = AnnotationNames {
            all_bound: true,
            bindings,
        };
        reads.visit_expr(&node.annotation);
        if !reads.all_bound {
            return None;
        }
    }
    let anchor = header_end(module, bindings)?;
    if std::ptr::eq(anchor, statement) {
        return None;
    }
    let is_logger = matches!(anchor, Stmt::Assign(_) | Stmt::AnnAssign(_));
    let mut offset = text.line_end_at(usize::from(anchor.end()));
    if !is_logger {
        // Keep the blank lines that separate the imports from the code.
        while offset < text.text.len()
            && python_strip(&text.text[offset..text.line_end_at(offset)]).is_empty()
        {
            offset = text.line_end_at(offset);
        }
    }
    let start = text.line_start(text.line_at(usize::from(statement.start())));
    let end = text.line_end_at(usize::from(statement.end()));
    if offset >= start {
        return None;
    }
    let mut moved = text.text[start..end].to_owned();
    if !moved.ends_with('\n') {
        moved.push('\n');
    }
    if !is_logger {
        // Separate the moved logger from the code it now precedes.
        moved.push('\n');
    }
    Some(fix(
        "safe",
        &format!("Move module logger {name} directly after the imports"),
        vec![
            text.insert(offset, &moved),
            text.delete_lines(statement.range()),
        ],
    ))
}

/// The last import, or logger right after the imports, that opens the module.
fn header_end<'a>(module: &'a ModModule, bindings: &HashMap<String, String>) -> Option<&'a Stmt> {
    let mut anchor = None;
    for (index, statement) in module.body.iter().enumerate() {
        if (index == 0 && is_docstring(statement))
            || guarded_imports(statement)
            || (anchor.is_some() && logger_target(statement, bindings).is_some())
        {
            anchor = Some(statement);
        } else {
            break;
        }
    }
    anchor
}

/// Whether every name an eagerly evaluated annotation reads is a module import.
struct AnnotationNames<'b> {
    all_bound: bool,
    bindings: &'b HashMap<String, String>,
}

impl<'a> Visitor<'a> for AnnotationNames<'_> {
    fn visit_expr(&mut self, node: &'a Expr) {
        if let Expr::Name(item) = node
            && !self.bindings.contains_key(item.id.as_str())
        {
            self.all_bound = false;
        }
        visitor::walk_expr(self, node);
    }
}

fn has_postponement_evidence(
    input: &RuleInput<'_>,
    module: &ModModule,
    minimum: Option<(u64, u64)>,
) -> bool {
    let mut annotations = Annotations { found: Vec::new() };
    annotations.visit_body(&module.body);
    if annotations.found.is_empty() {
        return false;
    }
    let mut sensitive = RuntimeSensitive { found: false };
    sensitive.visit_body(&module.body);
    if sensitive.found {
        return true;
    }
    let available = top_level_bindings(input, module);
    annotations.found.iter().any(|annotation| {
        let line = input.place(annotation.start()).0;
        needs_postponement(annotation, line, &available, minimum)
    })
}

fn needs_postponement(
    node: &Expr,
    line: usize,
    available: &[(&str, usize)],
    minimum: Option<(u64, u64)>,
) -> bool {
    let recurse = |item: &Expr| needs_postponement(item, line, available, minimum);
    match node {
        Expr::Name(item) => {
            let name = item.id.as_str();
            !BUILTIN_NAMES.contains(&name)
                && !available
                    .iter()
                    .any(|(bound, end)| *end < line && *bound == name)
        }
        Expr::NoneLiteral(_) => false,
        Expr::Subscript(item) => {
            let supported = minimum.is_some_and(|version| version >= GENERICS_VERSION)
                && matches!(item.value.as_ref(), Expr::Name(name)
                    if BUILTIN_GENERICS.contains(&name.id.as_str()));
            !supported || recurse(&item.value) || recurse(&item.slice)
        }
        Expr::BinOp(item) => {
            let supported = minimum.is_some_and(|version| version >= UNIONS_VERSION)
                && item.op == Operator::BitOr;
            !supported || recurse(&item.left) || recurse(&item.right)
        }
        Expr::Tuple(item) => item.elts.iter().any(recurse),
        _ => true,
    }
}

fn top_level_bindings<'a>(input: &RuleInput<'_>, module: &'a ModModule) -> Vec<(&'a str, usize)> {
    let mut bound = Vec::new();
    for statement in &module.body {
        let end = input.place(statement.end()).0;
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let name = match &alias.asname {
                        Some(local) => local.as_str(),
                        None => alias.name.split('.').next().unwrap_or_default(),
                    };
                    if alias.name.as_str() != "*" {
                        bound.push((name, end));
                    }
                }
            }
            Stmt::ImportFrom(node) => {
                for alias in &node.names {
                    if alias.name.as_str() != "*" {
                        bound.push((alias.asname.as_ref().unwrap_or(&alias.name).as_str(), end));
                    }
                }
            }
            Stmt::FunctionDef(node) => bound.push((node.name.as_str(), end)),
            Stmt::ClassDef(node) => bound.push((node.name.as_str(), end)),
            _ => {}
        }
    }
    bound
}

struct Annotations<'a> {
    found: Vec<&'a Expr>,
}

impl<'a> Visitor<'a> for Annotations<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => self.found.extend(node.returns.as_deref()),
            Stmt::AnnAssign(node) => self.found.push(&node.annotation),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_parameter(&mut self, parameter: &'a Parameter) {
        self.found.extend(parameter.annotation());
        visitor::walk_parameter(self, parameter);
    }
}

struct RuntimeSensitive {
    found: bool,
}

impl<'a> Visitor<'a> for RuntimeSensitive {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        self.found |= match statement {
            Stmt::Delete(_) => true,
            Stmt::FunctionDef(node) => !node.decorator_list.is_empty(),
            Stmt::ClassDef(node) => {
                !node.bases().is_empty()
                    || !node.keywords().is_empty()
                    || !node.decorator_list.is_empty()
            }
            _ => false,
        };
        if !self.found {
            visitor::walk_stmt(self, statement);
        }
    }

    fn visit_expr(&mut self, node: &'a Expr) {
        self.found |= match node {
            Expr::Name(item) => RUNTIME_NAMES.contains(&item.id.as_str()),
            Expr::Attribute(item) => RUNTIME_NAMES.contains(&item.attr.as_str()),
            _ => false,
        };
        if !self.found {
            visitor::walk_expr(self, node);
        }
    }
}
