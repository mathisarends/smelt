//! The top-level symbols, imports and strings of modules, from which `smelt init`
//! traces how an application is assembled.
use super::vfs;
use crate::checker::analysis::namespace::namespace_of;
use crate::checker::analysis::walk::{TreeNode, nodes_below};
use rayon::prelude::*;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{ExceptHandler, Expr, ExprContext, InterpolatedStringElement, Stmt};
use ruff_python_parser::parse_module;
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Deserialize)]
pub struct SymbolsRequest {
    files: Vec<ModuleFile>,
}

#[derive(Deserialize)]
struct ModuleFile {
    path: PathBuf,
    module: String,
    /// An `__init__.py`.
    is_package: bool,
}

/// The symbols of each requested file, in order, or null for one that does not parse.
pub fn symbols(request: &SymbolsRequest) -> Vec<Value> {
    request.files.par_iter().map(module_symbols).collect()
}

fn module_symbols(file: &ModuleFile) -> Value {
    let Ok(bytes) = vfs::read(&file.path) else {
        return Value::Null;
    };
    let body = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes);
    // Like Python's `read_text(errors="replace")` with universal newlines.
    let text = String::from_utf8_lossy(body)
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    let Ok(parsed) = parse_module(&text) else {
        return Value::Null;
    };
    let tree = parsed.syntax();
    let namespace = namespace_of(&text, tree, &file.module, file.is_package);
    let mut defined: Vec<&String> = namespace.defined.iter().collect();
    defined.sort();
    let mut references = HashMap::new();
    top_level_references(&tree.body, &mut references);
    let mut collector = Collector::default();
    collector.visit_body(&tree.body);
    json!({
        "imported": namespace.imported,
        "defined": defined,
        "references": references,
        "imports": collector.imports,
        "strings": collector.strings,
    })
}

/// Every dotted name each top-level definition reads, as
/// `smelt.analysis.exports.namespace_of` recorded them: `chat.feature` is
/// `["chat", "feature"]`, in `ast.walk` order.
fn top_level_references(body: &[Stmt], references: &mut HashMap<String, Vec<Vec<String>>>) {
    for statement in body {
        match statement {
            Stmt::If(node) => {
                top_level_references(&node.body, references);
                for clause in &node.elif_else_clauses {
                    top_level_references(&clause.body, references);
                }
            }
            Stmt::Try(node) if !node.is_star => {
                top_level_references(&node.body, references);
                top_level_references(&node.orelse, references);
                top_level_references(&node.finalbody, references);
                for handler in &node.handlers {
                    let ExceptHandler::ExceptHandler(handler) = handler;
                    top_level_references(&handler.body, references);
                }
            }
            Stmt::Import(node) => {
                for alias in &node.names {
                    let name = alias.asname.as_ref().map_or_else(
                        || alias.name.split('.').next().unwrap_or_default(),
                        |local| local.as_str(),
                    );
                    references.remove(name);
                }
            }
            Stmt::ImportFrom(node) => {
                for alias in &node.names {
                    if alias.name.as_str() != "*" {
                        let name = alias.asname.as_ref().unwrap_or(&alias.name);
                        references.remove(name.as_str());
                    }
                }
            }
            other => {
                let names = defined_names(other);
                if !names.is_empty() {
                    let dotted = dotted_names(other);
                    for name in names {
                        references.insert(name, dotted.clone());
                    }
                }
            }
        }
    }
}

fn defined_names(statement: &Stmt) -> Vec<String> {
    let targets: Vec<&Expr> = match statement {
        Stmt::FunctionDef(node) => return vec![node.name.to_string()],
        Stmt::ClassDef(node) => return vec![node.name.to_string()],
        Stmt::Assign(node) => node.targets.iter().collect(),
        Stmt::AnnAssign(node) => vec![node.target.as_ref()],
        Stmt::AugAssign(node) => vec![node.target.as_ref()],
        Stmt::TypeAlias(node) => vec![node.name.as_ref()],
        _ => return Vec::new(),
    };
    targets
        .into_iter()
        .flat_map(|target| nodes_below(TreeNode::Expression(target)))
        .filter_map(|node| match node {
            TreeNode::Expression(Expr::Name(name)) => Some(name.id.to_string()),
            _ => None,
        })
        .collect()
}

fn dotted_names(statement: &Stmt) -> Vec<Vec<String>> {
    nodes_below(TreeNode::Statement(statement))
        .into_iter()
        .filter_map(|node| match node {
            TreeNode::Expression(Expr::Attribute(attribute)) => {
                let mut parts = vec![attribute.attr.to_string()];
                let mut current = attribute.value.as_ref();
                while let Expr::Attribute(inner) = current {
                    parts.push(inner.attr.to_string());
                    current = inner.value.as_ref();
                }
                let Expr::Name(head) = current else {
                    return None;
                };
                parts.push(head.id.to_string());
                parts.reverse();
                Some(parts)
            }
            TreeNode::Expression(Expr::Name(name)) if name.ctx == ExprContext::Load => {
                Some(vec![name.id.to_string()])
            }
            _ => None,
        })
        .collect()
}

/// Every import statement and string constant of a module, nested ones included.
#[derive(Default)]
struct Collector {
    imports: Vec<Value>,
    strings: Vec<String>,
}

impl<'a> Visitor<'a> for Collector {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::Import(node) => {
                let names: Vec<&str> = node.names.iter().map(|alias| alias.name.as_str()).collect();
                self.imports.push(import(false, 0, None, &names));
            }
            Stmt::ImportFrom(node) => {
                let names: Vec<&str> = node.names.iter().map(|alias| alias.name.as_str()).collect();
                let module = node.module.as_ref().map(|name| name.as_str());
                self.imports.push(import(true, node.level, module, &names));
            }
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::StringLiteral(node) = expression {
            // Python joins implicitly concatenated literals into one constant.
            self.strings.push(node.value.to_str().to_owned());
            return;
        }
        visitor::walk_expr(self, expression);
    }

    fn visit_interpolated_string_element(&mut self, element: &'a InterpolatedStringElement) {
        if let InterpolatedStringElement::Literal(literal) = element {
            self.strings.push(literal.value.to_string());
        }
        visitor::walk_interpolated_string_element(self, element);
    }
}

fn import(from: bool, level: u32, module: Option<&str>, names: &[&str]) -> Value {
    json!({"from": from, "level": level, "module": module, "names": names})
}
