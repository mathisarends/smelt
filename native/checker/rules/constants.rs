//! LAY008: `layout/misplaced-private-constant`.
use super::{Finding, RuleInput};
use crate::checker::analysis::edits::{Skipped, fix, is_python_space, name_used};
use crate::checker::analysis::nodes::{is_constant, is_upper, walk_statements};
use crate::checker::analysis::walk::{TreeNode, nodes_below, tree_nodes};
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    Expr, ExprAttribute, ExprContext, ExprName, ModModule, Stmt, StmtClassDef, UnaryOp,
};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::HashMap;

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let mut found = Vec::new();
    for statement in walk_statements(module) {
        let Stmt::ClassDef(owner) = statement else {
            continue;
        };
        if !owner.bases().is_empty()
            || !owner.keywords().is_empty()
            || !owner.decorator_list.is_empty()
        {
            continue;
        }
        for member in &owner.body {
            let mut targets = ConstantTargets { found: Vec::new() };
            targets.visit_stmt(member);
            for target in targets.found {
                let repair = rewrite(input, module, owner, member);
                let (line, column) = input.place(target.start());
                let (end_line, end_column) = input.place(target.end());
                found.push(input.violation(Finding {
                    message: format!(
                        "move private constant {}.{} to module scope",
                        owner.name, target.id
                    ),
                    line,
                    column,
                    end_line: Some(end_line),
                    end_column: Some(end_column),
                    expected: json!({
                        "class": owner.name.as_str(),
                        "constant": target.id.as_str(),
                        "placement": "module",
                        "visibility": "private",
                        "automatic_rewrite": repair.is_some(),
                    }),
                    hint: format!(
                        "Declare {} at module scope and update its reads; review subclass overrides, mutation and consumers first.",
                        target.id
                    ),
                    fix: repair,
                }));
            }
        }
    }
    found
}

/// Private upper-case names assigned literals, outside nested definitions.
struct ConstantTargets<'a> {
    found: Vec<&'a ExprName>,
}

impl ConstantTargets<'_> {
    fn is_constant_name(target: &ExprName) -> bool {
        let name = target.id.as_str();
        name.starts_with('_') && !name.ends_with("__") && is_upper(name)
    }
}

impl<'a> Visitor<'a> for ConstantTargets<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => {}
            Stmt::Assign(node) => {
                if is_literal(&node.value) {
                    self.found.extend(
                        node.targets
                            .iter()
                            .filter_map(Expr::as_name_expr)
                            .filter(|target| Self::is_constant_name(target)),
                    );
                }
            }
            Stmt::AnnAssign(node) => {
                if node.value.as_deref().is_some_and(is_literal)
                    && let Expr::Name(target) = node.target.as_ref()
                    && Self::is_constant_name(target)
                {
                    self.found.push(target);
                }
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, _: &'a Expr) {}
}

fn is_literal(node: &Expr) -> bool {
    match node {
        Expr::Set(item) => item.elts.iter().all(is_literal),
        Expr::List(item) => item.elts.iter().all(is_literal),
        Expr::Tuple(item) => item.elts.iter().all(is_literal),
        Expr::Dict(item) => item
            .items
            .iter()
            .all(|entry| entry.key.as_ref().is_some_and(is_literal) && is_literal(&entry.value)),
        Expr::UnaryOp(item) if matches!(item.op, UnaryOp::UAdd | UnaryOp::USub) => {
            item.operand.is_number_literal_expr()
        }
        other => is_constant(other),
    }
}

/// Move the constant above its class and read it by name.
///
/// Unsafe: subclasses, instances and outside callers can no longer override or
/// reach it through the class. Refused unless every read is qualified by the
/// class or a method's receiver and the module name is otherwise unused.
fn rewrite(
    input: &RuleInput<'_>,
    module: &ModModule,
    owner: &StmtClassDef,
    statement: &Stmt,
) -> Option<Value> {
    let parsed = input.parsed;
    let text = &parsed.text;
    let name = match statement {
        Stmt::Assign(node) => match node.targets.as_slice() {
            [Expr::Name(target)] => target.id.as_str(),
            _ => return None,
        },
        Stmt::AnnAssign(node) if node.simple => node.target.as_name_expr()?.id.as_str(),
        _ => return None,
    };
    let is_top_level = module
        .body
        .iter()
        .any(|item| matches!(item, Stmt::ClassDef(node) if std::ptr::eq(node, owner)));
    if !is_top_level
        || owner.body.len() == 1
        || name.starts_with("__")
        || !text.stands_alone(statement.range())
        || name_used(parsed, name, &[Skipped::Statement(statement)])
    {
        return None;
    }
    let reads = qualified_reads(module, owner, name)?;
    let start = text.line_start(text.line_at(usize::from(statement.start())));
    let mut end = text.line_end(text.line_at(usize::from(statement.end())));
    if text.text[end..text.line_end_at(end)]
        .chars()
        .all(is_python_space)
    {
        end = text.line_end_at(end);
    }
    let statement_start = usize::from(statement.start());
    let declaration = dedent(&format!(
        "{}{}",
        &text.text[start..statement_start],
        text.segment(statement.range())
    ));
    let mut edits = vec![
        text.insert(
            text.block_start(usize::from(owner.start())),
            &format!("{declaration}\n\n\n"),
        ),
        text.edit(start, end, ""),
    ];
    edits.extend(reads.iter().map(|read| text.replace(read.range(), name)));
    Some(fix(
        "unsafe",
        &format!("Move {name} to module scope as a private constant"),
        edits,
    ))
}

/// Reads of `Class.name` anywhere and `self.name` in the class methods.
///
/// `None` when any other access, or any write, could reach the attribute.
fn qualified_reads<'a>(
    module: &'a ModModule,
    owner: &StmtClassDef,
    name: &str,
) -> Option<Vec<&'a ExprAttribute>> {
    let mut receivers: HashMap<*const Expr, &str> = HashMap::new();
    for member in &owner.body {
        let Stmt::FunctionDef(method) = member else {
            continue;
        };
        let parameters = &method.parameters;
        let Some(first) = parameters.posonlyargs.iter().chain(&parameters.args).next() else {
            continue;
        };
        for node in nodes_below(TreeNode::Statement(member)) {
            if let TreeNode::Expression(expression) = node {
                receivers.insert(
                    std::ptr::from_ref(expression),
                    first.parameter.name.as_str(),
                );
            }
        }
    }
    let mut reads = Vec::new();
    for node in tree_nodes(module) {
        let TreeNode::Expression(expression) = node else {
            continue;
        };
        let Expr::Attribute(attribute) = expression else {
            continue;
        };
        if attribute.attr.as_str() != name {
            continue;
        }
        if attribute.ctx != ExprContext::Load {
            return None;
        }
        let Expr::Name(base) = attribute.value.as_ref() else {
            return None;
        };
        let base = base.id.as_str();
        if base != owner.name.as_str()
            && receivers.get(&std::ptr::from_ref(expression)).copied() != Some(base)
        {
            return None;
        }
        reads.push(attribute);
    }
    Some(reads)
}

/// `textwrap.dedent`, as Python 3.14 implements it.
fn dedent(text: &str) -> String {
    let is_blank = |line: &str| !line.is_empty() && line.chars().all(is_python_space);
    let lines: Vec<&str> = text.split('\n').collect();
    let content = || {
        lines
            .iter()
            .copied()
            .filter(|line| !line.is_empty() && !is_blank(line))
    };
    let margin = match (content().min(), content().max()) {
        (Some(first), Some(last)) => first
            .bytes()
            .zip(last.bytes())
            .take_while(|(left, right)| left == right && matches!(left, b' ' | b'\t'))
            .count(),
        _ => 0,
    };
    let kept: Vec<&str> = lines
        .iter()
        .map(|line| {
            if is_blank(line) {
                ""
            } else {
                line.get(margin..).unwrap_or("")
            }
        })
        .collect();
    kept.join("\n")
}
