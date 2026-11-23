//! Static outcome evidence in checked, inline Python subprocesses.
use super::TestFunction;
use crate::checker::analysis::walk::TreeNode;
use ruff_python_ast::{Expr, ExprCall, Stmt};
use ruff_python_parser::parse_module;

const CHECKED: [&str; 3] = [
    "subprocess.run",
    "subprocess.check_call",
    "subprocess.check_output",
];
const FLAGS: [&str; 4] = ["-P", "-I", "-B", "-u"];
const COMMAND_PARTS: usize = 3;
const SCRIPT_ARGUMENTS: usize = 2;

pub fn checked_python_assertions(
    call: &ExprCall,
    qualified: &str,
    resolve: &dyn Fn(&Expr) -> Option<String>,
    text: &dyn Fn(&Expr) -> Option<String>,
) -> bool {
    if !checked_options(call, qualified, text) {
        return false;
    }
    let command = call.arguments.args.first().or_else(|| {
        call.arguments
            .keywords
            .iter()
            .find(|keyword| {
                keyword
                    .arg
                    .as_ref()
                    .is_some_and(|arg| arg.as_str() == "args")
            })
            .map(|keyword| &keyword.value)
    });
    let Some(source) = command.and_then(|command| python_source(command, resolve, text)) else {
        return false;
    };
    if source.contains('\0') {
        return false;
    }
    let Ok(parsed) = parse_module(&source) else {
        return false;
    };
    parsed.syntax().body.iter().any(|statement| {
        matches!(statement, Stmt::Assert(item)
            if !matches!(item.test.as_ref(), Expr::BooleanLiteral(value) if value.value))
    })
}

pub fn child_status_propagates<'a>(call: &'a Expr, function: &TestFunction<'a>) -> bool {
    let mut node = TreeNode::Expression(call);
    while let Some(parent) = function.parent(node) {
        if matches!(parent, TreeNode::Statement(Stmt::Try(_) | Stmt::With(_))) {
            return false;
        }
        node = parent;
    }
    true
}

fn option<'a>(call: &'a ExprCall, name: &str) -> Option<&'a Expr> {
    // A repeated keyword parses; the last one wins like in a dict.
    call.arguments
        .keywords
        .iter()
        .rev()
        .find(|keyword| keyword.arg.as_ref().is_some_and(|arg| arg.as_str() == name))
        .map(|keyword| &keyword.value)
}

fn checked_options(
    call: &ExprCall,
    qualified: &str,
    text: &dyn Fn(&Expr) -> Option<String>,
) -> bool {
    if !CHECKED.contains(&qualified)
        || call
            .arguments
            .keywords
            .iter()
            .any(|keyword| keyword.arg.is_none())
    {
        return false;
    }
    if qualified == "subprocess.run"
        && !matches!(option(call, "check"), Some(Expr::BooleanLiteral(value)) if value.value)
    {
        return false;
    }
    if option(call, "executable").is_some() {
        return false;
    }
    if option(call, "shell")
        .is_some_and(|shell| !matches!(shell, Expr::BooleanLiteral(value) if !value.value))
    {
        return false;
    }
    match option(call, "env") {
        None => true,
        Some(Expr::Dict(environment)) => !environment.items.iter().any(|item| {
            item.key.as_ref().is_some_and(|key| {
                text(key).as_deref() == Some("PYTHONOPTIMIZE")
                    && text(&item.value)
                        .as_deref()
                        .is_none_or(|value| !matches!(value, "" | "0"))
            })
        }),
        Some(_) => false,
    }
}

fn python_source(
    command: &Expr,
    resolve: &dyn Fn(&Expr) -> Option<String>,
    text: &dyn Fn(&Expr) -> Option<String>,
) -> Option<String> {
    let elements = match command {
        Expr::List(item) => &item.elts,
        Expr::Tuple(item) => &item.elts,
        _ => return None,
    };
    if elements.len() < COMMAND_PARTS {
        return None;
    }
    if resolve(&elements[0]).as_deref() != Some("sys.executable") {
        return None;
    }
    let mut arguments = &elements[1..];
    while let Some(first) = arguments.first() {
        if text(first).is_some_and(|value| FLAGS.contains(&value.as_str())) {
            arguments = &arguments[1..];
        } else {
            break;
        }
    }
    if arguments.len() != SCRIPT_ARGUMENTS || text(&arguments[0]).as_deref() != Some("-c") {
        return None;
    }
    text(&arguments[1])
}
