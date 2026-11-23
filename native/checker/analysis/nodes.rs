//! Syntax helpers shared by the ported rules.
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ModExpression, ModModule, Stmt, StmtClassDef};
use ruff_python_parser::{Parsed, parse_expression};
use ruff_text_size::Ranged;

/// `ast.parse(text, mode="eval")`: Ruff alone accepts more than CPython does here.
pub fn parse_eval(text: &str) -> Option<Parsed<ModExpression>> {
    let first_line = text
        .split('\n')
        .find(|line| !line.trim().is_empty() && !line.trim_start().starts_with('#'))?;
    if first_line.starts_with([' ', '\t', '\x0c']) {
        return None;
    }
    let parsed = parse_expression(text).ok()?;
    let body = parsed.expr();
    let starred = match body {
        Expr::Starred(_) => true,
        Expr::Tuple(tuple) => tuple.elts.iter().any(Expr::is_starred_expr),
        _ => false,
    };
    let bare = matches!(body, Expr::Named(_) | Expr::Yield(_) | Expr::YieldFrom(_))
        && !text[..usize::from(body.start())].contains('(');
    (!starred && !bare).then_some(parsed)
}

/// The items of a subscript: tuple elements, or the slice itself.
pub fn subscript_parts(slice: &Expr) -> &[Expr] {
    match slice {
        Expr::Tuple(tuple) => &tuple.elts,
        other => std::slice::from_ref(other),
    }
}

/// Every class in the module, however deeply nested.
pub fn all_classes(module: &ModModule) -> Vec<&StmtClassDef> {
    let mut collector = ClassCollector { found: Vec::new() };
    collector.visit_body(&module.body);
    collector.found
}

struct ClassCollector<'a> {
    found: Vec<&'a StmtClassDef>,
}

impl<'a> Visitor<'a> for ClassCollector<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if let Stmt::ClassDef(node) = statement {
            self.found.push(node);
        }
        visitor::walk_stmt(self, statement);
    }
}
