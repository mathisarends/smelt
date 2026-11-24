//! File-wide name binding counts, as the syntax analyses use them.
use super::namespace::{namespace_of, resolve_relative};
use super::nodes::{parse_eval, walk_statements};
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    ExceptHandler, Expr, ExprContext, ModModule, Parameter, Pattern, Stmt, TypeParam,
};
use std::borrow::Borrow;
use std::collections::{HashMap, HashSet};
use std::hash::Hash;

const TYPING_MODULES: [&str; 2] = ["typing", "typing_extensions"];

pub type Names<'a> = HashMap<&'a str, String>;

/// Which constructs count as binding a name.
#[derive(Clone, Copy, PartialEq)]
pub enum Coverage {
    /// Everything `binding_counts` treats as a binding.
    Full,
    /// Only direct bindings: no type parameters, mapping rests or attribute writes.
    Direct,
}

pub struct BindingCounts<'a> {
    counts: HashMap<&'a str, usize>,
}

impl<'a> BindingCounts<'a> {
    pub fn new(module: &'a ModModule, coverage: Coverage) -> Self {
        let mut counter = Counter {
            counts: HashMap::new(),
            coverage,
        };
        counter.visit_body(&module.body);
        Self {
            counts: counter.counts,
        }
    }

    /// `binding_counts(ast.Module(body=statements))`.
    pub fn of_statements(statements: impl IntoIterator<Item = &'a Stmt>) -> Self {
        let mut counter = Counter {
            counts: HashMap::new(),
            coverage: Coverage::Full,
        };
        for statement in statements {
            counter.visit_stmt(statement);
        }
        Self {
            counts: counter.counts,
        }
    }

    pub fn get(&self, name: &str) -> usize {
        self.counts.get(name).copied().unwrap_or(0)
    }

    /// Every name bound at least once.
    pub fn names(&self) -> impl Iterator<Item = &'a str> + '_ {
        self.counts.keys().copied()
    }
}

struct Counter<'a> {
    counts: HashMap<&'a str, usize>,
    coverage: Coverage,
}

impl<'a> Counter<'a> {
    fn add(&mut self, name: &'a str) {
        *self.counts.entry(name).or_insert(0) += 1;
    }
}

impl<'a> Visitor<'a> for Counter<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => self.add(node.name.as_str()),
            Stmt::ClassDef(node) => self.add(node.name.as_str()),
            Stmt::Import(node) => {
                for alias in &node.names {
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    self.add(name.split('.').next().unwrap_or(name));
                }
            }
            Stmt::ImportFrom(node) => {
                for alias in &node.names {
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    self.add(name.split('.').next().unwrap_or(name));
                }
            }
            Stmt::Global(node) => {
                for name in &node.names {
                    self.add(name.as_str());
                }
            }
            Stmt::Nonlocal(node) => {
                for name in &node.names {
                    self.add(name.as_str());
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_except_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        if let Some(name) = &node.name {
            self.add(name.as_str());
        }
        visitor::walk_except_handler(self, handler);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Name(node) if node.ctx != ExprContext::Load => self.add(node.id.as_str()),
            Expr::Attribute(node)
                if self.coverage == Coverage::Full && node.ctx != ExprContext::Load =>
            {
                if let Some(root) = root_name(&node.value) {
                    self.add(root);
                }
            }
            Expr::Subscript(node)
                if self.coverage == Coverage::Full && node.ctx != ExprContext::Load =>
            {
                if let Some(root) = root_name(&node.value) {
                    self.add(root);
                }
            }
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }

    fn visit_parameter(&mut self, parameter: &'a Parameter) {
        self.add(parameter.name.as_str());
        visitor::walk_parameter(self, parameter);
    }

    fn visit_type_param(&mut self, parameter: &'a TypeParam) {
        if self.coverage == Coverage::Full {
            self.add(parameter.name().as_str());
        }
        visitor::walk_type_param(self, parameter);
    }

    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        match pattern {
            Pattern::MatchAs(node) => {
                if let Some(name) = &node.name {
                    self.add(name.as_str());
                }
            }
            Pattern::MatchStar(node) => {
                if let Some(name) = &node.name {
                    self.add(name.as_str());
                }
            }
            Pattern::MatchMapping(node) if self.coverage == Coverage::Full => {
                if let Some(rest) = &node.rest {
                    self.add(rest.as_str());
                }
            }
            _ => {}
        }
        visitor::walk_pattern(self, pattern);
    }
}

/// The name at the root of an attribute or item chain.
pub fn root_name(expression: &Expr) -> Option<&str> {
    match expression {
        Expr::Name(node) => Some(node.id.as_str()),
        Expr::Attribute(node) => root_name(&node.value),
        Expr::Subscript(node) => root_name(&node.value),
        _ => None,
    }
}

/// The imported dotted name `expression` refers to, if it is known.
pub fn qualified_name<K>(expression: &Expr, bindings: &HashMap<K, String>) -> Option<String>
where
    K: Borrow<str> + Eq + Hash,
{
    match expression {
        Expr::Name(node) => bindings.get(node.id.as_str()).cloned(),
        Expr::Attribute(node) => {
            qualified_name(&node.value, bindings).map(|parent| format!("{parent}.{}", node.attr))
        }
        _ => None,
    }
}

/// A name or attribute chain as written.
pub fn dotted_name(expression: &Expr) -> Option<String> {
    match expression {
        Expr::Name(node) => Some(node.id.to_string()),
        Expr::Attribute(node) => {
            dotted_name(&node.value).map(|owner| format!("{owner}.{}", node.attr))
        }
        _ => None,
    }
}

/// The last segment of a name or attribute chain, or `""` for other expressions.
pub fn dotted_tail(expression: &Expr) -> String {
    let dotted = dotted_name(expression).unwrap_or_default();
    dotted.rsplit('.').next().unwrap_or_default().to_owned()
}

/// The outer type of a direct annotation, including a quoted one.
pub fn annotation_name<K>(annotation: &Expr, bindings: &HashMap<K, String>) -> Option<String>
where
    K: Borrow<str> + Eq + Hash,
{
    if let Expr::StringLiteral(node) = annotation {
        let parsed = parse_eval(node.value.to_str())?;
        return outer_name(parsed.expr(), bindings);
    }
    outer_name(annotation, bindings)
}

fn outer_name<K>(node: &Expr, bindings: &HashMap<K, String>) -> Option<String>
where
    K: Borrow<str> + Eq + Hash,
{
    match node {
        Expr::Subscript(item) => qualified_name(&item.value, bindings),
        other => qualified_name(other, bindings),
    }
}

/// Module imports and requested builtins, skipping any name bound again in the file.
///
/// Wildcard imports are unknown; relative imports and re-exports are not resolved.
pub fn unshadowed_bindings(
    source: &str,
    module: &ModModule,
    builtin_names: &[&str],
) -> HashMap<String, String> {
    let counts = BindingCounts::new(module, Coverage::Full);
    if counts.get("*") > 0 {
        return HashMap::new();
    }
    let imported = namespace_of(source, module, "", false).imported;
    let mut relative = HashSet::new();
    for statement in walk_statements(module) {
        if let Stmt::ImportFrom(node) = statement
            && node.level > 0
        {
            for alias in &node.names {
                relative.insert(alias.asname.as_ref().unwrap_or(&alias.name).as_str());
            }
        }
    }
    let mut bindings: HashMap<String, String> = builtin_names
        .iter()
        .filter(|name| counts.get(name) == 0)
        .map(|name| ((*name).to_owned(), format!("builtins.{name}")))
        .collect();
    for (name, target) in imported {
        if counts.get(&name) == 1 && !relative.contains(name.as_str()) {
            bindings.insert(name, target);
        }
    }
    bindings
}

/// Local name to imported name for every import in the file, in `ast.walk` order.
pub fn import_bindings<'a>(module: &'a ModModule, name: &str, is_package: bool) -> Names<'a> {
    let mut bindings = Names::new();
    for statement in walk_statements(module) {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    if let Some(local) = &alias.asname {
                        bindings.insert(local.as_str(), alias.name.to_string());
                    } else {
                        let full = alias.name.as_str();
                        let top = full.split('.').next().unwrap_or(full);
                        bindings.entry(top).or_insert_with(|| top.to_owned());
                    }
                }
            }
            Stmt::ImportFrom(node) => {
                let base = resolve_relative(
                    name,
                    is_package,
                    node.level,
                    node.module.as_ref().map(|target| target.as_str()),
                );
                for alias in &node.names {
                    if alias.name.as_str() == "*" {
                        continue;
                    }
                    let qualified = if base.is_empty() {
                        alias.name.to_string()
                    } else {
                        format!("{base}.{}", alias.name)
                    };
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    bindings.insert(local, qualified);
                }
            }
            _ => {}
        }
    }
    bindings
}

/// Direct, unshadowed imports of `typing` and `typing_extensions`.
pub fn typing_bindings(module: &ModModule) -> Names<'_> {
    let bound = BindingCounts::new(module, Coverage::Full);
    let mut bindings = Names::new();
    if bound.get("*") > 0 {
        return bindings;
    }
    for statement in &module.body {
        match statement {
            Stmt::Import(node) => {
                for alias in &node.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    if TYPING_MODULES.contains(&alias.name.as_str()) && bound.get(local) == 1 {
                        bindings.insert(local, alias.name.to_string());
                    }
                }
            }
            Stmt::ImportFrom(node) if node.level == 0 => {
                let Some(source) = node.module.as_ref().map(|name| name.as_str()) else {
                    continue;
                };
                if !TYPING_MODULES.contains(&source) {
                    continue;
                }
                for alias in &node.names {
                    let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    if bound.get(local) == 1 {
                        bindings.insert(local, format!("{source}.{}", alias.name));
                    }
                }
            }
            _ => {}
        }
    }
    bindings
}

/// The typing member `expression` names, such as `Literal`.
pub fn typing_name<'a>(expression: &'a Expr, bindings: &'a Names<'_>) -> Option<&'a str> {
    match expression {
        Expr::Name(node) => {
            let (module, name) = bindings.get(node.id.as_str())?.rsplit_once('.')?;
            TYPING_MODULES.contains(&module).then_some(name)
        }
        Expr::Attribute(node) => {
            let Expr::Name(owner) = node.value.as_ref() else {
                return None;
            };
            let module = bindings.get(owner.id.as_str())?;
            TYPING_MODULES
                .contains(&module.as_str())
                .then_some(node.attr.as_str())
        }
        _ => None,
    }
}
