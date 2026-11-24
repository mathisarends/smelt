//! Module-qualified reads through stable lexical import bindings, like
//! `smelt.analysis.module_access`.
use super::namespace::resolve_relative;
use crate::checker::source::ParsedSource;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    Comprehension, ExceptHandler, Expr, ExprAttribute, ExprContext, Parameters, Pattern, Stmt,
    TypeParams,
};
use ruff_text_size::Ranged;
use std::collections::{HashMap, HashSet};

/// `module.attribute` read through a stable import binding.
pub struct ModuleRead<'a> {
    pub node: &'a ExprAttribute,
    /// The dotted module the receiver is bound to.
    pub qualified: String,
}

/// The module-qualified reads of a module; reads on `ignored` lines are left out.
pub fn module_attributes<'a>(
    parsed: &'a ParsedSource,
    module: &str,
    is_package: bool,
    ignored: &HashSet<usize>,
) -> Vec<ModuleRead<'a>> {
    let mut attributes = Attributes {
        parsed,
        module,
        is_package,
        ignored,
        env: HashMap::new(),
        closure: HashMap::new(),
        in_class: false,
        reads: Vec::new(),
    };
    attributes.scope(
        Body::Statements(&parsed.module().body),
        HashMap::new(),
        HashSet::new(),
        false,
    );
    attributes.reads
}

type Env = HashMap<String, String>;

#[derive(Clone, Copy)]
enum Body<'a> {
    Statements(&'a [Stmt]),
    Expression(&'a Expr),
}

struct Attributes<'a, 'c> {
    parsed: &'a ParsedSource,
    module: &'c str,
    is_package: bool,
    ignored: &'c HashSet<usize>,
    env: Env,
    closure: Env,
    in_class: bool,
    reads: Vec<ModuleRead<'a>>,
}

impl<'a> Attributes<'a, '_> {
    fn outer(&self) -> Env {
        if self.in_class {
            self.closure.clone()
        } else {
            self.env.clone()
        }
    }

    fn scope(&mut self, body: Body<'a>, outer: Env, bound: HashSet<String>, in_class: bool) {
        let mut bindings = Bindings {
            parsed: self.parsed,
            module: self.module,
            is_package: self.is_package,
            ignored: self.ignored,
            counts: HashMap::new(),
            imports: HashMap::new(),
            star: false,
        };
        for name in bound {
            *bindings.counts.entry(name).or_insert(0) += 1;
        }
        match body {
            Body::Statements(statements) => {
                for statement in statements {
                    bindings.visit_stmt(statement);
                }
            }
            Body::Expression(expression) => bindings.visit_expr(expression),
        }
        let mut env: Env = if bindings.star {
            HashMap::new()
        } else {
            outer
                .iter()
                .filter(|(name, _)| !bindings.counts.contains_key(*name))
                .map(|(name, value)| (name.clone(), value.clone()))
                .collect()
        };
        if !bindings.star {
            for (name, value) in bindings.imports {
                if bindings.counts.get(&name) == Some(&1) {
                    env.insert(name, value);
                }
            }
        }
        let previous_env = std::mem::replace(&mut self.env, env);
        let previous_closure = std::mem::replace(&mut self.closure, outer);
        let previous_class = std::mem::replace(&mut self.in_class, in_class);
        match body {
            Body::Statements(statements) => {
                for statement in statements {
                    self.visit_stmt(statement);
                }
            }
            Body::Expression(expression) => self.visit_expr(expression),
        }
        self.env = previous_env;
        self.closure = previous_closure;
        self.in_class = previous_class;
    }

    fn comprehension(&mut self, generators: &'a [Comprehension], elements: &[&'a Expr]) {
        let Some(first) = generators.first() else {
            return;
        };
        self.visit_expr(&first.iter);
        let mut bound = HashSet::new();
        for generator in generators {
            collect_names(&generator.target, &mut bound);
        }
        let outer = self.outer();
        let env = outer
            .into_iter()
            .filter(|(name, _)| !bound.contains(name.as_str()))
            .collect();
        let previous = std::mem::replace(&mut self.env, env);
        for (index, generator) in generators.iter().enumerate() {
            if index > 0 {
                self.visit_expr(&generator.iter);
            }
            for condition in &generator.ifs {
                self.visit_expr(condition);
            }
        }
        for element in elements {
            self.visit_expr(element);
        }
        self.env = previous;
    }
}

impl<'a> Visitor<'a> for Attributes<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => {
                for decorator in &node.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                self.visit_parameters(&node.parameters);
                if let Some(parameters) = &node.type_params {
                    self.visit_type_params(parameters);
                }
                if let Some(returns) = &node.returns {
                    self.visit_expr(returns);
                }
                let outer = self.outer();
                let mut bound = parameter_names(&node.parameters);
                bound.extend(type_parameter_names(node.type_params.as_deref()));
                self.scope(Body::Statements(&node.body), outer, bound, false);
            }
            Stmt::ClassDef(node) => {
                for decorator in &node.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                if let Some(arguments) = &node.arguments {
                    for base in &arguments.args {
                        self.visit_expr(base);
                    }
                    for keyword in &arguments.keywords {
                        self.visit_expr(&keyword.value);
                    }
                }
                let outer = self.outer();
                let bound = type_parameter_names(node.type_params.as_deref());
                self.scope(Body::Statements(&node.body), outer, bound, true);
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Attribute(node) => {
                let line = self.parsed.text.line_at(usize::from(node.start()));
                if node.ctx == ExprContext::Load
                    && !self.ignored.contains(&line)
                    && let Some(qualified) = qualified(&node.value, &self.env)
                    && !qualified.is_empty()
                {
                    self.reads.push(ModuleRead { node, qualified });
                }
                visitor::walk_expr(self, expression);
            }
            Expr::Lambda(node) => {
                if let Some(parameters) = &node.parameters {
                    self.visit_parameters(parameters);
                }
                let outer = self.outer();
                let bound = node
                    .parameters
                    .as_deref()
                    .map(parameter_names)
                    .unwrap_or_default();
                self.scope(Body::Expression(&node.body), outer, bound, false);
            }
            Expr::ListComp(node) => self.comprehension(&node.generators, &[&node.elt]),
            Expr::SetComp(node) => self.comprehension(&node.generators, &[&node.elt]),
            Expr::Generator(node) => self.comprehension(&node.generators, &[&node.elt]),
            Expr::DictComp(node) => {
                let mut elements: Vec<&Expr> = node.key.as_deref().into_iter().collect();
                elements.push(&node.value);
                self.comprehension(&node.generators, &elements);
            }
            other => visitor::walk_expr(self, other),
        }
    }
}

fn qualified(node: &Expr, env: &Env) -> Option<String> {
    match node {
        Expr::Name(name) => env.get(name.id.as_str()).cloned(),
        Expr::Attribute(attribute) => {
            let base = qualified(&attribute.value, env)?;
            (!base.is_empty()).then(|| format!("{base}.{}", attribute.attr))
        }
        _ => None,
    }
}

fn parameter_names(parameters: &Parameters) -> HashSet<String> {
    parameters
        .iter()
        .map(|parameter| parameter.name().to_string())
        .collect()
}

fn type_parameter_names(parameters: Option<&TypeParams>) -> HashSet<String> {
    parameters
        .map(|parameters| {
            parameters
                .type_params
                .iter()
                .map(|parameter| parameter.name().to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn collect_names(expression: &Expr, names: &mut HashSet<String>) {
    struct Names<'n> {
        names: &'n mut HashSet<String>,
    }
    impl<'a> Visitor<'a> for Names<'_> {
        fn visit_expr(&mut self, expression: &'a Expr) {
            if let Expr::Name(name) = expression {
                self.names.insert(name.id.to_string());
            }
            visitor::walk_expr(self, expression);
        }
    }
    Names { names }.visit_expr(expression);
}

/// The names one scope binds, and the imports among them.
struct Bindings<'a, 'c> {
    parsed: &'a ParsedSource,
    module: &'c str,
    is_package: bool,
    ignored: &'c HashSet<usize>,
    counts: HashMap<String, usize>,
    imports: HashMap<String, String>,
    star: bool,
}

impl Bindings<'_, '_> {
    fn count(&mut self, name: &str) {
        *self.counts.entry(name.to_owned()).or_insert(0) += 1;
    }

    fn ignored(&self, statement: &Stmt) -> bool {
        let line = self.parsed.text.line_at(usize::from(statement.start()));
        self.ignored.contains(&line)
    }
}

impl<'a> Visitor<'a> for Bindings<'_, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::Import(node) => {
                let ignored = self.ignored(statement);
                for alias in &node.names {
                    let name = alias.asname.as_ref().map_or_else(
                        || alias.name.split('.').next().unwrap_or_default(),
                        |name| name.as_str(),
                    );
                    self.count(name);
                    if !ignored {
                        let target = if alias.asname.is_some() {
                            alias.name.as_str()
                        } else {
                            name
                        };
                        self.imports.insert(name.to_owned(), target.to_owned());
                    }
                }
            }
            Stmt::ImportFrom(node) => {
                let ignored = self.ignored(statement);
                let base = resolve_relative(
                    self.module,
                    self.is_package,
                    node.level,
                    node.module.as_ref().map(|name| name.as_str()),
                );
                for alias in &node.names {
                    if alias.name.as_str() == "*" {
                        self.star = true;
                    }
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    self.count(name);
                    if !ignored {
                        self.imports
                            .insert(name.to_owned(), format!("{base}.{}", alias.name));
                    }
                }
            }
            Stmt::FunctionDef(node) => {
                self.count(node.name.as_str());
                for decorator in &node.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                self.visit_parameters(&node.parameters);
                if let Some(returns) = &node.returns {
                    self.visit_expr(returns);
                }
            }
            Stmt::ClassDef(node) => {
                self.count(node.name.as_str());
                for decorator in &node.decorator_list {
                    self.visit_expr(&decorator.expression);
                }
                if let Some(arguments) = &node.arguments {
                    for base in &arguments.args {
                        self.visit_expr(base);
                    }
                    for keyword in &arguments.keywords {
                        self.visit_expr(&keyword.value);
                    }
                }
            }
            Stmt::Global(node) => {
                for name in &node.names {
                    self.count(name.as_str());
                }
            }
            Stmt::Nonlocal(node) => {
                for name in &node.names {
                    self.count(name.as_str());
                }
            }
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Name(node) => {
                if matches!(node.ctx, ExprContext::Store | ExprContext::Del) {
                    self.count(node.id.as_str());
                }
            }
            Expr::Lambda(node) => {
                if let Some(parameters) = &node.parameters {
                    self.visit_parameters(parameters);
                }
            }
            other => visitor::walk_expr(self, other),
        }
    }

    fn visit_comprehension(&mut self, comprehension: &'a Comprehension) {
        self.visit_expr(&comprehension.iter);
        for condition in &comprehension.ifs {
            self.visit_expr(condition);
        }
    }

    fn visit_except_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        if let Some(name) = &node.name {
            self.count(name.as_str());
        }
        visitor::walk_except_handler(self, handler);
    }

    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        match pattern {
            Pattern::MatchAs(node) => {
                if let Some(name) = &node.name {
                    self.count(name.as_str());
                }
                visitor::walk_pattern(self, pattern);
            }
            Pattern::MatchStar(node) => {
                if let Some(name) = &node.name {
                    self.count(name.as_str());
                }
            }
            Pattern::MatchMapping(node) => {
                if let Some(rest) = &node.rest {
                    self.count(rest.as_str());
                }
                visitor::walk_pattern(self, pattern);
            }
            other => visitor::walk_pattern(self, other),
        }
    }
}
