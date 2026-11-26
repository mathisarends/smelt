//! VIS003: `visibility/misplaced-dunder-all` and LAY010: `layout/reexport-only-module`.
use super::{Finding, RuleInput};
use crate::checker::analysis::nodes::{
    has_docstring, walk_expr_in_field_order, walk_stmt_in_field_order,
};
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Alias, Expr, ExprContext, ModModule, Operator, Stmt};
use ruff_text_size::{Ranged, TextSize};
use serde_json::json;
use std::collections::HashSet;

const DUNDER_ALL: &str = "__all__";

pub fn check_dunder_all(input: &RuleInput<'_>) -> Vec<Violation> {
    if input.source.is_package {
        return Vec::new();
    }
    let mut bindings = ModuleBindings { found: Vec::new() };
    bindings.visit_body(&input.parsed.module().body);
    let mut seen = HashSet::new();
    let mut found = Vec::new();
    for offset in bindings.found {
        let (line, column) = input.place(offset);
        if !seen.insert(line) {
            continue;
        }
        found.push(input.violation(Finding {
            message: "declare __all__ in __init__.py instead of an ordinary module".to_owned(),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({
                "allowed_filename": "__init__.py",
                "automatic_removal": false,
            }),
            hint: "Review moving the package export declaration to __init__.py; preserve star-import compatibility and check facade cycles.".to_owned(),
            fix: None,
        }));
    }
    found
}

pub fn check_reexports(input: &RuleInput<'_>) -> Vec<Violation> {
    if input.source.is_package {
        return Vec::new();
    }
    let bindings = reexport_bindings(input.parsed.module());
    let Some((_, first)) = bindings.first() else {
        return Vec::new();
    };
    let (line, column) = input.place(first.start());
    let mut names: Vec<&str> = bindings.iter().map(|(name, _)| *name).collect();
    names.sort_unstable();
    vec![input.violation(Finding {
        message: format!("{} only re-exports imported names", input.source.module),
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({"reexported_names": names}),
        hint: "Review using the defining module or package API directly. Preserve supported import paths with a reasoned exception.".to_owned(),
        fix: None,
    })]
}

/// Module-scope bindings of `__all__`, in the order Python's AST lists them.
struct ModuleBindings {
    found: Vec<TextSize>,
}

impl<'a> Visitor<'a> for ModuleBindings {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if !matches!(statement, Stmt::FunctionDef(_) | Stmt::ClassDef(_)) {
            walk_stmt_in_field_order(self, statement);
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Lambda(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::Generator(_) => return,
            Expr::Name(node)
                if node.ctx == ExprContext::Store && node.id.as_str() == DUNDER_ALL =>
            {
                self.found.push(node.start());
            }
            _ => {}
        }
        walk_expr_in_field_order(self, expression);
    }

    fn visit_alias(&mut self, alias: &'a Alias) {
        if alias.asname.as_ref().unwrap_or(&alias.name).as_str() == DUNDER_ALL {
            self.found.push(alias.start());
        }
    }
}

fn reexport_bindings(tree: &ModModule) -> Vec<(&str, &Alias)> {
    let body = if has_docstring(&tree.body) {
        &tree.body[1..]
    } else {
        &tree.body[..]
    };
    let mut bindings = Vec::new();
    let mut names = HashSet::new();
    let mut declares_all = false;
    for statement in body {
        if let Stmt::ImportFrom(node) = statement
            && node.level == 0
            && node
                .module
                .as_ref()
                .is_some_and(|name| name.as_str() == "__future__")
        {
            continue;
        }
        if !declares_all && is_literal_export(statement) {
            declares_all = true;
            continue;
        }
        let aliases = match statement {
            Stmt::Import(node) => &node.names,
            Stmt::ImportFrom(node) => &node.names,
            _ => return Vec::new(),
        };
        for alias in aliases {
            let name = match &alias.asname {
                Some(local) => local.as_str(),
                None => {
                    let full = alias.name.as_str();
                    full.split('.').next().unwrap_or(full)
                }
            };
            if alias.name.as_str() == "*"
                || !names.insert(name)
                || matches!(name, DUNDER_ALL | "__getattr__" | "__dir__")
            {
                return Vec::new();
            }
            bindings.push((name, alias));
        }
    }
    let explicit = if declares_all {
        explicit_exports(tree).is_some_and(|exports| exports == names)
    } else {
        bindings.iter().all(|(_, alias)| {
            alias
                .asname
                .as_ref()
                .is_some_and(|local| local.as_str() == alias.name.as_str())
        })
    };
    if explicit { bindings } else { Vec::new() }
}

fn is_literal_export(statement: &Stmt) -> bool {
    let Stmt::Assign(node) = statement else {
        return false;
    };
    matches!(node.targets.as_slice(), [target] if is_dunder_all(target))
        && matches!(node.value.as_ref(), Expr::List(_) | Expr::Tuple(_))
}

fn is_dunder_all(expression: &Expr) -> bool {
    matches!(expression, Expr::Name(name) if name.id.as_str() == DUNDER_ALL)
}

/// The names `__all__` declares; `None` means a dynamic declaration.
pub(crate) fn explicit_exports(tree: &ModModule) -> Option<HashSet<&str>> {
    static_export_declarations(tree).map(|(exports, _)| exports)
}

/// Static exports and the number of assignments to the export list.
pub(super) fn static_export_declarations(tree: &ModModule) -> Option<(HashSet<&str>, usize)> {
    let mut exports = HashSet::new();
    let mut handled = 0;
    for statement in &tree.body {
        let (targets, value): (&[Expr], Option<&Expr>) = match statement {
            Stmt::Assign(node) => (&node.targets, Some(&node.value)),
            Stmt::AnnAssign(node) => (
                std::slice::from_ref(node.target.as_ref()),
                node.value.as_deref(),
            ),
            Stmt::AugAssign(node) => (
                std::slice::from_ref(node.target.as_ref()),
                Some(&node.value),
            ),
            _ => continue,
        };
        let names = targets.iter().filter(|item| is_dunder_all(item)).count();
        if names == 0 {
            continue;
        }
        let items = match value? {
            Expr::List(node) => &node.elts,
            Expr::Tuple(node) => &node.elts,
            Expr::Set(node) => &node.elts,
            _ => return None,
        };
        let declared = items
            .iter()
            .map(|item| match item {
                Expr::StringLiteral(text) => Some(text.value.to_str()),
                _ => None,
            })
            .collect::<Option<Vec<_>>>()?;
        match statement {
            Stmt::AugAssign(node) if node.op != Operator::Add => return None,
            Stmt::AugAssign(_) => {}
            _ => exports.clear(),
        }
        exports.extend(declared);
        handled += names;
    }
    let mut uses = DunderAllUses { count: 0 };
    uses.visit_body(&tree.body);
    (uses.count == handled).then_some((exports, handled))
}

struct DunderAllUses {
    count: usize,
}

impl<'a> Visitor<'a> for DunderAllUses {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if is_dunder_all(expression) {
            self.count += 1;
        }
        visitor::walk_expr(self, expression);
    }
}
