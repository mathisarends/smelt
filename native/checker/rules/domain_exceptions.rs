//! MOD008: `modeling/prefer-domain-exception`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{Names, import_bindings, qualified_name};
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ModModule, Stmt, StmtFunctionDef, StmtRaise};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::HashSet;

const DEFAULT_ALLOWED: [&str; 6] = [
    "NotImplementedError",
    "StopIteration",
    "StopAsyncIteration",
    "GeneratorExit",
    "KeyboardInterrupt",
    "SystemExit",
];
const DOMAIN_DUNDERS: [&str; 4] = ["__init__", "__post_init__", "__new__", "__call__"];
const VALIDATORS: [&str; 6] = [
    "pydantic.field_validator",
    "pydantic.model_validator",
    "pydantic.validator",
    "pydantic.root_validator",
    "pydantic.functional_validators.field_validator",
    "pydantic.functional_validators.model_validator",
];
/// Exception classes in `builtins` on CPython 3.14, sorted.
///
/// Python reads them from the running interpreter, so it omits `WindowsError` off Windows and
/// `PythonFinalizationError` and `_IncompleteInputError` before 3.13.
const BUILTIN_EXCEPTIONS: &[&str] = &[
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "BaseExceptionGroup",
    "BlockingIOError",
    "BrokenPipeError",
    "BufferError",
    "BytesWarning",
    "ChildProcessError",
    "ConnectionAbortedError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "DeprecationWarning",
    "EOFError",
    "EncodingWarning",
    "EnvironmentError",
    "Exception",
    "ExceptionGroup",
    "FileExistsError",
    "FileNotFoundError",
    "FloatingPointError",
    "FutureWarning",
    "GeneratorExit",
    "IOError",
    "ImportError",
    "ImportWarning",
    "IndentationError",
    "IndexError",
    "InterruptedError",
    "IsADirectoryError",
    "KeyError",
    "KeyboardInterrupt",
    "LookupError",
    "MemoryError",
    "ModuleNotFoundError",
    "NameError",
    "NotADirectoryError",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "PendingDeprecationWarning",
    "PermissionError",
    "ProcessLookupError",
    "PythonFinalizationError",
    "RecursionError",
    "ReferenceError",
    "ResourceWarning",
    "RuntimeError",
    "RuntimeWarning",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "SyntaxWarning",
    "SystemError",
    "SystemExit",
    "TabError",
    "TimeoutError",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeError",
    "UnicodeTranslateError",
    "UnicodeWarning",
    "UserWarning",
    "ValueError",
    "Warning",
    "WindowsError",
    "ZeroDivisionError",
    "_IncompleteInputError",
];

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let source = input.source;
    let Some(origin) = input.contracts.policy_for(&source.module, "errors") else {
        return Vec::new();
    };
    let Some(base) = origin.value.get("base").and_then(Value::as_str) else {
        return Vec::new();
    };
    let allowed: HashSet<&str> = origin
        .value
        .get("allow")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .chain(DEFAULT_ALLOWED)
        .collect();
    let module = input.parsed.module();
    let bindings = import_bindings(module, &source.module, source.is_package);
    let mut shadowed = module_names(module);
    shadowed.extend(bindings.keys().copied());
    let mut raises = Raises {
        bindings: &bindings,
        found: Vec::new(),
    };
    raises.visit_body(&module.body);
    let mut raised = raises.found;
    raised.sort_by_key(|node| node.start());
    let (package, short) = base.rsplit_once('.').unwrap_or(("", base));
    let mut found = Vec::new();
    for node in raised {
        let Some(exception) = node.exc.as_deref() else {
            continue;
        };
        let Some(name) = builtin(exception, &shadowed, &bindings) else {
            continue;
        };
        if allowed.contains(name.as_str()) {
            continue;
        }
        let (line, column) = input.place(node.start());
        found.push(input.violation(Finding {
            message: format!(
                "Raise of builtin {name}; {} expects a subclass of {base}",
                origin.label
            ),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "contract": origin.contract,
                "exception": name,
                "base": base,
                "automatic_rewrite": false,
            }),
            hint: format!(
                "Raise a subclass of {short} from {package} that names the failure, or add {name} to errors.allow when callers rely on it."
            ),
            fix: None,
        }));
    }
    found
}

/// Raises with an exception, outside protocol dunders and pydantic validators.
struct Raises<'a, 'b> {
    bindings: &'b Names<'a>,
    found: Vec<&'a StmtRaise>,
}

impl<'a> Visitor<'a> for Raises<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) if is_exempt(node, self.bindings) => return,
            Stmt::Raise(node) if node.exc.is_some() => self.found.push(node),
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, _expression: &'a Expr) {}
}

fn is_exempt(function: &StmtFunctionDef, bindings: &Names<'_>) -> bool {
    let name = function.name.as_str();
    if name.starts_with("__") && name.ends_with("__") && !DOMAIN_DUNDERS.contains(&name) {
        return true;
    }
    function.decorator_list.iter().any(|decorator| {
        let target = match &decorator.expression {
            Expr::Call(call) => call.func.as_ref(),
            other => other,
        };
        qualified_name(target, bindings)
            .is_some_and(|qualified| VALIDATORS.contains(&qualified.as_str()))
    })
}

fn is_builtin_exception(name: &str) -> bool {
    BUILTIN_EXCEPTIONS.binary_search(&name).is_ok()
}

fn builtin(exception: &Expr, shadowed: &HashSet<&str>, bindings: &Names<'_>) -> Option<String> {
    let exception = match exception {
        Expr::Call(call) => call.func.as_ref(),
        other => other,
    };
    if let Expr::Name(node) = exception {
        let name = node.id.as_str();
        return (is_builtin_exception(name) && !shadowed.contains(name)).then(|| name.to_owned());
    }
    let qualified = qualified_name(exception, bindings)?;
    let name = qualified.strip_prefix("builtins.")?;
    is_builtin_exception(name).then(|| name.to_owned())
}

fn module_names(tree: &ModModule) -> HashSet<&str> {
    let mut names = HashSet::new();
    for statement in &tree.body {
        let targets: &[Expr] = match statement {
            Stmt::ClassDef(node) => {
                names.insert(node.name.as_str());
                continue;
            }
            Stmt::FunctionDef(node) => {
                names.insert(node.name.as_str());
                continue;
            }
            Stmt::Assign(node) => &node.targets,
            Stmt::AnnAssign(node) => std::slice::from_ref(node.target.as_ref()),
            _ => continue,
        };
        names.extend(targets.iter().filter_map(|target| match target {
            Expr::Name(name) => Some(name.id.as_str()),
            _ => None,
        }));
    }
    names
}
