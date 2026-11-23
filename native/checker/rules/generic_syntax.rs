//! TYP008: `typing/prefer-inline-type-parameters`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{
    BindingCounts, Coverage, Names, typing_bindings, typing_name,
};
use crate::checker::analysis::edits::{
    Skipped, fix, is_python_space, is_word_boundary, name_used, python_strip, remove_aliases,
};
use crate::checker::analysis::nodes::{subscript_parts, walk_statements};
use crate::checker::violation::Violation;
use ruff_python_ast::{Alias, Expr, ExprCall, Stmt, StmtClassDef};
use ruff_text_size::{Ranged, TextRange};
use serde_json::{Value, json};

const INLINE_TYPE_PARAMETERS: (u64, u64) = (3, 12);
const DECLARATIONS: [&str; 3] = ["TypeVar", "ParamSpec", "TypeVarTuple"];
const HINT: &str = "Use PEP 695 class type parameters in place of the Generic base after checking inferred variance and shared type variable uses.";

struct Declared<'a> {
    parameter: String,
    name: &'a str,
    statement: &'a Stmt,
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let target = input.targets.for_path(&input.source.absolute);
    if target
        .minimum
        .is_none_or(|minimum| minimum < INLINE_TYPE_PARAMETERS)
    {
        return Vec::new();
    }
    let module = input.parsed.module();
    let bindings = typing_bindings(module);
    let mut found = Vec::new();
    for statement in walk_statements(module) {
        let Stmt::ClassDef(node) = statement else {
            continue;
        };
        let Some(generic) = node
            .arguments
            .iter()
            .flat_map(|arguments| arguments.args.iter())
            .find(|base| is_generic(base, &bindings))
        else {
            continue;
        };
        let repair = rewrite(input, statement, node, generic, &bindings);
        let (line, column) = input.place(input.parsed.class_start(node));
        found.push(input.violation(Finding {
            message: format!("declare type parameters inline on class {}", node.name),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "class": node.name.as_str(),
                "python": target.to_json(),
                "automatic_rewrite": repair.is_some(),
            }),
            hint: HINT.to_owned(),
            fix: repair,
        }));
    }
    found
}

fn is_generic(base: &Expr, bindings: &Names<'_>) -> bool {
    matches!(base, Expr::Subscript(item) if typing_name(&item.value, bindings) == Some("Generic"))
}

/// Move the Generic parameters onto the class declaration.
///
/// Unsafe: PEP 695 parameters infer their variance, and the class gets its own
/// parameter objects instead of the shared module-level ones.
fn rewrite(
    input: &RuleInput<'_>,
    statement: &Stmt,
    node: &StmtClassDef,
    generic: &Expr,
    bindings: &Names<'_>,
) -> Option<Value> {
    if node.type_params.is_some() {
        return None;
    }
    let Expr::Subscript(subscript) = generic else {
        return None;
    };
    let parsed = input.parsed;
    let module = parsed.module();
    let text = &parsed.text;
    let counts = BindingCounts::new(module, Coverage::Full);
    let declared: Vec<Declared<'_>> = subscript_parts(&subscript.slice)
        .iter()
        .map(|element| declaration(input, element, bindings, &counts))
        .collect::<Option<_>>()?;
    let parameters: Vec<&str> = declared
        .iter()
        .map(|item| item.parameter.as_str())
        .collect();
    let mut edits = vec![header(input, node, generic, &parameters)?];
    let mut removed = vec![Skipped::Expression(generic)];
    for item in &declared {
        let ignored = [
            Skipped::Statement(statement),
            Skipped::Statement(item.statement),
        ];
        if name_used(parsed, item.name, &ignored) {
            continue;
        }
        if !text.stands_alone(item.statement.range()) {
            return None;
        }
        edits.push(text.delete_lines(item.statement.range()));
        removed.push(Skipped::Statement(item.statement));
    }
    edits.extend(unused_imports(input, &removed, bindings));
    Some(fix(
        "unsafe",
        &format!("Declare the type parameters of {} inline", node.name),
        edits,
    ))
}

/// The inline parameter, its name and its module-level declaration.
fn declaration<'a>(
    input: &RuleInput<'a>,
    element: &Expr,
    bindings: &Names<'_>,
    counts: &BindingCounts<'_>,
) -> Option<Declared<'a>> {
    let (starred, inner) = match element {
        Expr::Starred(node) => (true, node.value.as_ref()),
        other => (false, other),
    };
    let Expr::Name(target) = inner else {
        return None;
    };
    let wanted = target.id.as_str();
    if counts.get(wanted) != 1 {
        return None;
    }
    for statement in &input.parsed.module().body {
        let Stmt::Assign(assignment) = statement else {
            continue;
        };
        let [Expr::Name(bound)] = assignment.targets.as_slice() else {
            continue;
        };
        let Expr::Call(call) = assignment.value.as_ref() else {
            continue;
        };
        if bound.id.as_str() != wanted {
            continue;
        }
        let kind = typing_name(&call.func, bindings)?;
        if !is_translatable(kind, starred, wanted, call) {
            return None;
        }
        return Some(Declared {
            parameter: parameter(input, kind, wanted, call),
            name: bound.id.as_str(),
            statement,
        });
    }
    None
}

fn is_translatable(kind: &str, starred: bool, name: &str, call: &ExprCall) -> bool {
    let arguments = &call.arguments;
    DECLARATIONS.contains(&kind)
        && (kind == "TypeVarTuple") == starred
        && matches!(arguments.args.first(), Some(Expr::StringLiteral(first)) if first.value.to_str() == name)
        && !arguments.args.iter().any(Expr::is_starred_expr)
        && (kind == "TypeVar" || arguments.args.len() <= 1)
        && arguments.keywords.iter().all(|keyword| {
            kind == "TypeVar"
                && keyword
                    .arg
                    .as_ref()
                    .is_some_and(|arg| arg.as_str() == "bound")
        })
}

fn parameter(input: &RuleInput<'_>, kind: &str, name: &str, call: &ExprCall) -> String {
    let text = &input.parsed.text;
    match kind {
        "ParamSpec" => return format!("**{name}"),
        "TypeVarTuple" => return format!("*{name}"),
        _ => {}
    }
    let constraints: Vec<&str> = call.arguments.args[1..]
        .iter()
        .map(|argument| text.segment(input.parsed.comprehension_range(argument)))
        .collect();
    if !constraints.is_empty() {
        return format!("{name}: ({})", constraints.join(", "));
    }
    match call.arguments.keywords.first() {
        Some(keyword) => format!(
            "{name}: {}",
            text.segment(input.parsed.comprehension_range(&keyword.value))
        ),
        None => name.to_owned(),
    }
}

/// Add the parameters after the class name and drop the Generic base.
fn header(
    input: &RuleInput<'_>,
    node: &StmtClassDef,
    generic: &Expr,
    parameters: &[&str],
) -> Option<Value> {
    let text = &input.parsed.text;
    let content = text.text.as_str();
    let start = usize::from(input.parsed.class_start(node));
    let name_end = class_name_end(content, start, node.name.as_str())?;
    let listed = format!("[{}]", parameters.join(", "));
    let class_arguments = node.arguments.as_ref()?;
    let mut arguments: Vec<TextRange> = class_arguments
        .args
        .iter()
        .map(Ranged::range)
        .chain(class_arguments.keywords.iter().map(Ranged::range))
        .collect();
    arguments.sort_by_key(|range| range.start());
    let base_start = usize::from(generic.start());
    let base_end = usize::from(generic.end());
    if arguments.len() == 1 {
        let closing = base_end + content[base_end..].find(')')?;
        let inside = content[base_end..closing].trim_matches([',', ' ', '\t', '\n']);
        if python_strip(&content[name_end..base_start]) != "(" || !inside.is_empty() {
            return None;
        }
        return Some(text.edit(name_end, closing + 1, &listed));
    }
    let index = arguments
        .iter()
        .position(|range| *range == generic.range())?;
    let (span_start, span_end) = match arguments.get(index + 1) {
        Some(next) => (base_start, usize::from(next.start())),
        None => (usize::from(arguments[index - 1].end()), base_end),
    };
    if content[span_start..span_end].contains('#') {
        return None;
    }
    if python_strip(&content[name_end..usize::from(arguments[0].start())]) != "(" {
        return None;
    }
    // One edit keeps the insertion and the base removal from overlapping.
    let replacement = format!("{listed}{}", &content[name_end..span_start]);
    Some(text.edit(name_end, span_end, &replacement))
}

/// The end of `class\s+NAME\b` at `start`.
fn class_name_end(content: &str, start: usize, name: &str) -> Option<usize> {
    let after = content[start..].strip_prefix("class")?;
    let rest = after.trim_start_matches(is_python_space);
    if rest.len() == after.len() {
        return None;
    }
    let tail = rest.strip_prefix(name)?;
    is_word_boundary(name.chars().next_back(), tail.chars().next())
        .then_some(content.len() - tail.len())
}

/// Remove Generic and declaration imports that only the removed code used.
fn unused_imports(
    input: &RuleInput<'_>,
    removed: &[Skipped<'_>],
    bindings: &Names<'_>,
) -> Vec<Value> {
    let parsed = input.parsed;
    let mut edits = Vec::new();
    for statement in &parsed.module().body {
        let Stmt::ImportFrom(node) = statement else {
            continue;
        };
        let unused: Vec<&Alias> = node
            .names
            .iter()
            .filter(|alias| {
                let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                let name = alias.name.as_str();
                bindings.contains_key(local)
                    && (name == "Generic" || DECLARATIONS.contains(&name))
                    && !name_used(parsed, local, removed)
            })
            .collect();
        if !unused.is_empty()
            && let Some(edit) = remove_aliases(&parsed.text, statement, &unused)
        {
            edits.push(edit);
        }
    }
    edits
}
