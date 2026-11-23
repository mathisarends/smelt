//! MOD006: `modeling/missing-dataclass-option`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::dotted_tail;
use crate::checker::analysis::edits::fix;
use crate::checker::analysis::nodes::walk_statements;
use crate::checker::violation::Violation;
use ruff_python_ast::{Expr, ModModule, Stmt, StmtClassDef};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};

/// `dir(builtins)` on CPython 3.14 with `site` loaded, sorted.
pub(super) const BUILTIN_NAMES: [&str; 160] = [
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
    "Ellipsis",
    "EncodingWarning",
    "EnvironmentError",
    "Exception",
    "ExceptionGroup",
    "False",
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
    "None",
    "NotADirectoryError",
    "NotImplemented",
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
    "True",
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
    "__build_class__",
    "__debug__",
    "__doc__",
    "__import__",
    "__loader__",
    "__name__",
    "__package__",
    "__spec__",
    "abs",
    "aiter",
    "all",
    "anext",
    "any",
    "ascii",
    "bin",
    "bool",
    "breakpoint",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "classmethod",
    "compile",
    "complex",
    "copyright",
    "credits",
    "delattr",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "eval",
    "exec",
    "exit",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "globals",
    "hasattr",
    "hash",
    "help",
    "hex",
    "id",
    "input",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "license",
    "list",
    "locals",
    "map",
    "max",
    "memoryview",
    "min",
    "next",
    "object",
    "oct",
    "open",
    "ord",
    "pow",
    "print",
    "property",
    "quit",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "slice",
    "sorted",
    "staticmethod",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "vars",
    "zip",
];
// These bases declare empty __slots__, so slots still pay off below them.
const SLOTTED_MODULES: [&str; 2] = ["abc", "typing"];

/// Names bound by imports from outside the project, and names defined inside it.
struct ExternalNames<'a> {
    imported: HashSet<&'a str>,
    local: HashSet<&'a str>,
}

impl ExternalNames<'_> {
    fn contains(&self, base: &Expr) -> bool {
        let root = match base {
            Expr::Subscript(_) | Expr::Attribute(_) => chain_root(base),
            other => other,
        };
        let Expr::Name(node) = root else {
            return false;
        };
        let name = node.id.as_str();
        self.imported.contains(name)
            || (!self.local.contains(name)
                && name != "object"
                && BUILTIN_NAMES.binary_search(&name).is_ok())
    }
}

fn chain_root(mut node: &Expr) -> &Expr {
    loop {
        node = match node {
            Expr::Subscript(item) => &item.value,
            Expr::Attribute(item) => &item.value,
            other => return other,
        };
    }
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let Some(origin) = input
        .contracts
        .policy_for(&input.source.module, "dataclass_options")
    else {
        return Vec::new();
    };
    let Some(required) = origin.value.as_array().filter(|items| !items.is_empty()) else {
        return Vec::new();
    };
    let required: Vec<&str> = required.iter().filter_map(Value::as_str).collect();
    let module = input.parsed.module();
    let external = external_names(module, input.roots);
    let mut found = Vec::new();
    for statement in walk_statements(module) {
        let Stmt::ClassDef(owner) = statement else {
            continue;
        };
        let missing = missing_options(owner, &required, &external);
        if missing.is_empty() {
            continue;
        }
        let options = spelled(&missing);
        let repair = rewrite(input, owner, &missing);
        let (line, column) = input.place(input.parsed.class_start(owner));
        found.push(input.violation(Finding {
            message: format!(
                "Dataclass {} is missing {options}; {} requires it",
                owner.name, origin.label
            ),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "contract": origin.contract,
                "class": owner.name.as_str(),
                "dataclass_options": required,
                "missing": missing,
                "automatic_rewrite": repair.is_some(),
            }),
            hint: format!(
                "Add {options} to the @dataclass decorator of {}.",
                owner.name
            ),
            fix: repair,
        }));
    }
    found
}

fn spelled(options: &[&str]) -> String {
    let parts: Vec<String> = options.iter().map(|item| format!("{item}=True")).collect();
    parts.join(", ")
}

fn missing_options<'a>(
    owner: &StmtClassDef,
    required: &[&'a str],
    external: &ExternalNames<'_>,
) -> Vec<&'a str> {
    let Some(decorator) = dataclass_decorator(owner) else {
        return Vec::new();
    };
    let keywords = decorator_keywords(decorator);
    let mut missing = Vec::new();
    for option in required {
        let satisfied = keywords
            .get(option)
            .is_some_and(|value| !matches!(value, Expr::BooleanLiteral(item) if !item.value));
        if satisfied {
            continue;
        }
        let has_external_base = owner
            .arguments
            .iter()
            .flat_map(|arguments| arguments.args.iter())
            .any(|base| external.contains(base));
        if *option == "slots" && (has_external_base || caches_properties(owner)) {
            continue;
        }
        missing.push(*option);
    }
    missing
}

fn decorator_keywords(decorator: &Expr) -> HashMap<&str, &Expr> {
    let Expr::Call(call) = decorator else {
        return HashMap::new();
    };
    call.arguments
        .keywords
        .iter()
        .filter_map(|item| Some((item.arg.as_ref()?.as_str(), &item.value)))
        .collect()
}

/// `functools.cached_property` stores its value in `__dict__`, which slots remove.
fn caches_properties(owner: &StmtClassDef) -> bool {
    owner.body.iter().any(|method| {
        matches!(method, Stmt::FunctionDef(function) if function
            .decorator_list
            .iter()
            .any(|decorator| dotted_tail(&decorator.expression) == "cached_property"))
    })
}

fn dataclass_decorator(owner: &StmtClassDef) -> Option<&Expr> {
    owner
        .decorator_list
        .iter()
        .map(|decorator| &decorator.expression)
        .find(|expression| {
            let target = match expression {
                Expr::Call(call) => call.func.as_ref(),
                other => other,
            };
            dotted_tail(target) == "dataclass"
        })
}

/// Set each missing option to True on the dataclass decorator.
///
/// Unsafe: freezing, slotting or keyword-only construction breaks callers that
/// mutate instances, add attributes or pass fields positionally.
fn rewrite(input: &RuleInput<'_>, owner: &StmtClassDef, missing: &[&str]) -> Option<Value> {
    let parsed = input.parsed;
    let text = &parsed.text;
    let decorator = dataclass_decorator(owner)?;
    let options = spelled(missing);
    let mut edits = Vec::new();
    if let Expr::Call(call) = decorator {
        let arguments = &call.arguments;
        if arguments
            .keywords
            .iter()
            .any(|keyword| keyword.arg.is_none())
        {
            return None;
        }
        let present = decorator_keywords(decorator);
        let mut added = Vec::new();
        for option in missing {
            match present.get(option) {
                Some(value) => edits.push(text.replace(parsed.comprehension_range(value), "True")),
                None => added.push(*option),
            }
        }
        let last = arguments
            .args
            .iter()
            .map(|argument| parsed.comprehension_range(argument).end())
            .chain(arguments.keywords.iter().map(Ranged::end))
            .max();
        if !added.is_empty() {
            let listed = spelled(&added);
            if let Some(end) = last {
                edits.push(text.insert(usize::from(end), &format!(", {listed}")));
            } else {
                let end = usize::from(decorator.end());
                if !text.text[..end].ends_with(')') {
                    return None;
                }
                edits.push(text.insert(end - 1, &listed));
            }
        }
    } else {
        edits.push(text.insert(usize::from(decorator.end()), &format!("({options})")));
    }
    Some(fix(
        "unsafe",
        &format!("Set {options} on {}", owner.name),
        edits,
    ))
}

fn external_names<'a>(module: &'a ModModule, roots: &[String]) -> ExternalNames<'a> {
    let mut imported = HashSet::new();
    let mut local = HashSet::new();
    for statement in walk_statements(module) {
        match statement {
            Stmt::ImportFrom(node) => {
                let source = node.module.as_ref().map(|name| name.as_str());
                let target = match source {
                    Some(name) if node.level == 0 && is_external(name, roots) => &mut imported,
                    _ => &mut local,
                };
                target.extend(
                    node.names
                        .iter()
                        .map(|alias| alias.asname.as_ref().unwrap_or(&alias.name).as_str()),
                );
            }
            Stmt::Import(node) => {
                for alias in &node.names {
                    let bound = match &alias.asname {
                        Some(name) => name.as_str(),
                        None => alias.name.split('.').next().unwrap_or_default(),
                    };
                    if is_external(alias.name.as_str(), roots) {
                        imported.insert(bound);
                    } else {
                        local.insert(bound);
                    }
                }
            }
            Stmt::ClassDef(node) => {
                local.insert(node.name.as_str());
            }
            Stmt::Assign(node) => {
                if let [Expr::Name(target)] = node.targets.as_slice() {
                    local.insert(target.id.as_str());
                }
            }
            _ => {}
        }
    }
    ExternalNames { imported, local }
}

fn is_external(module: &str, roots: &[String]) -> bool {
    let top = module.split('.').next().unwrap_or(module);
    !roots.iter().any(|root| root == top) && !SLOTTED_MODULES.contains(&top)
}
