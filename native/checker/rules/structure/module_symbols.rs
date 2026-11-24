//! VIS002: public module constants and PEP 695 aliases used only inside their module.
use super::members::{is_composition_root, private_name, rename_advice};
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::members::MemberUsageIndex;
use crate::checker::analysis::nodes::{is_upper, parse_eval};
use crate::checker::analysis::walk::{TreeNode, nodes_below};
use crate::checker::rules::explicit_exports;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    ExceptHandler, Expr, ExprContext, FStringPartRef, InterpolatedStringElement, ModModule,
    Parameter, Pattern, Stmt, StmtClassDef, StmtFunctionDef, TypeParam,
};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{BTreeSet, HashMap, HashSet};

const REFLECTION: [&str; 5] = ["globals", "locals", "vars", "eval", "exec"];

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let usage = context.member_usage();
    let scope = MemberUsageIndex::usage_scope(context.project);
    let mut sources: Vec<_> = context
        .sources
        .iter()
        .filter(|(source, _)| !source.is_package)
        .collect();
    sources.sort_by(|left, right| left.0.path.cmp(&right.0.path));
    let mut found = Vec::new();
    for (source, parsed) in sources {
        let module = source.module.as_str();
        if is_composition_root(context, module) {
            continue;
        }
        let tree = parsed.module();
        let candidates: Vec<(&str, &Expr, &str)> = candidates(tree)
            .into_iter()
            .filter(|(name, _, _)| {
                !name.starts_with('_') && !usage.module_name_used_externally(module, name)
            })
            .collect();
        if candidates.is_empty() || reflective(tree) {
            continue;
        }
        let Some(exports) = explicit_exports(tree) else {
            continue;
        };
        let bindings = binding_counts(tree);
        let mut loads: HashMap<&str, BTreeSet<usize>> = HashMap::new();
        for node in nodes_below_module(tree) {
            if let TreeNode::Expression(Expr::Name(name)) = node
                && name.ctx == ExprContext::Load
            {
                loads
                    .entry(name.id.as_str())
                    .or_default()
                    .insert(parsed.text.line_at(name.start().into()));
            }
        }
        let api = api_names(tree, module, &exports, usage);
        let occupied: HashSet<String> = bindings.keys().map(|name| (*name).to_owned()).collect();
        for (name, target, kind) in candidates {
            if bindings.get(name) != Some(&1) || api.contains(name) {
                continue;
            }
            let suggested = private_name(name, &occupied);
            let (line, column) = parsed.text.position(target.start().into());
            let references: Vec<usize> = loads.get(name).into_iter().flatten().copied().collect();
            found.push(StructureFinding {
                message: format!(
                    "{name} is public, but has no observed use outside its module or public-signature evidence"
                ),
                path: source.path.clone(),
                position: Some((line, column + 1)),
                source_module: Some(module.to_owned()),
                target_module: Some(format!("{module}.{name}")),
                expected: json!({
                    "kind": kind,
                    "member": name,
                    "suggested_name": suggested,
                    "internal_references": references,
                    "usage_scope": scope,
                    "automatic_rename": false,
                }),
                hint: format!(
                    "Unless public by design, {}; otherwise export it or suppress VIS002 with a reason.",
                    rename_advice(name, &suggested)
                ),
                ..StructureFinding::default()
            });
        }
    }
    found
}

fn nodes_below_module(tree: &ModModule) -> Vec<TreeNode<'_>> {
    tree.body
        .iter()
        .flat_map(|statement| nodes_below(TreeNode::Statement(statement)))
        .collect()
}

/// Single unconditional uppercase assignments and type aliases at top level.
fn candidates(tree: &ModModule) -> Vec<(&str, &Expr, &'static str)> {
    let mut found = Vec::new();
    for statement in &tree.body {
        let (target, kind) = match statement {
            Stmt::TypeAlias(node) => (node.name.as_ref(), "type-alias"),
            Stmt::Assign(node) if node.targets.len() == 1 => (&node.targets[0], "constant"),
            Stmt::AnnAssign(node) if node.value.is_some() => (node.target.as_ref(), "constant"),
            _ => continue,
        };
        if let Expr::Name(name) = target
            && (kind == "type-alias" || is_upper(name.id.as_str()))
        {
            found.push((name.id.as_str(), target, kind));
        }
    }
    found
}

fn api_names(
    tree: &ModModule,
    module: &str,
    exports: &HashSet<&str>,
    usage: &MemberUsageIndex<'_>,
) -> HashSet<String> {
    let mut api: HashSet<String> = exports.iter().map(|name| (*name).to_owned()).collect();
    for statement in &tree.body {
        let name = match statement {
            Stmt::FunctionDef(node) => node.name.as_str(),
            Stmt::ClassDef(node) => node.name.as_str(),
            Stmt::TypeAlias(node) => match node.name.as_ref() {
                Expr::Name(name) => name.id.as_str(),
                _ => continue,
            },
            _ => continue,
        };
        if usage.module_name_used_externally(module, name) {
            api.insert(name.to_owned());
        }
    }
    let mut collector = Api { names: api };
    collector.body(&tree.body, false);
    let mut api = collector.names;
    let aliases: HashMap<&str, &Stmt> = tree
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::TypeAlias(node) => match node.name.as_ref() {
                Expr::Name(name) => Some((name.id.as_str(), statement)),
                _ => None,
            },
            _ => None,
        })
        .collect();
    let mut pending: Vec<String> = api.iter().cloned().collect();
    while let Some(name) = pending.pop() {
        let Some(alias) = aliases.get(name.as_str()) else {
            continue;
        };
        let dependencies: Vec<String> = references(TreeNode::Statement(alias))
            .into_iter()
            .filter(|name| !api.contains(name))
            .collect();
        api.extend(dependencies.iter().cloned());
        pending.extend(dependencies);
    }
    api
}

/// The names a public signature or class body exposes, added as they are found:
/// a private definition exported by an earlier signature exposes its own.
struct Api {
    names: HashSet<String>,
}

impl Api {
    fn emit(&mut self, expression: &Expr) {
        let found = references(TreeNode::Expression(expression));
        self.names.extend(found);
    }

    /// `_api_expressions`; inside a class nothing counts as exported.
    fn body(&mut self, body: &[Stmt], in_class: bool) {
        for statement in body {
            self.statement(statement, in_class);
        }
    }

    fn statement(&mut self, statement: &Stmt, in_class: bool) {
        let skipped = |name: &str, names: &HashSet<String>| {
            let exported = !in_class && names.contains(name);
            name.starts_with('_')
                && !exported
                && !(in_class && name.starts_with("__") && name.ends_with("__"))
        };
        match statement {
            Stmt::FunctionDef(node) => {
                if !skipped(node.name.as_str(), &self.names) {
                    self.function(node);
                }
            }
            Stmt::ClassDef(node) => {
                if !skipped(node.name.as_str(), &self.names) {
                    self.class(node);
                }
            }
            Stmt::AnnAssign(node) if matches!(node.target.as_ref(), Expr::Name(name) if !name.id.starts_with('_')) =>
            {
                self.emit(&node.annotation);
                if in_class && let Some(value) = &node.value {
                    self.emit(value);
                }
            }
            Stmt::Assign(node)
                if in_class
                    && node.targets.iter().any(
                        |target| matches!(target, Expr::Name(name) if !name.id.starts_with('_')),
                    ) =>
            {
                self.emit(&node.value);
            }
            Stmt::TypeAlias(_) => {}
            Stmt::If(node) => {
                self.body(&node.body, in_class);
                for clause in &node.elif_else_clauses {
                    self.body(&clause.body, in_class);
                }
            }
            Stmt::For(node) => {
                self.body(&node.body, in_class);
                self.body(&node.orelse, in_class);
            }
            Stmt::While(node) => {
                self.body(&node.body, in_class);
                self.body(&node.orelse, in_class);
            }
            Stmt::With(node) => self.body(&node.body, in_class),
            Stmt::Try(node) => {
                self.body(&node.body, in_class);
                for handler in &node.handlers {
                    let ExceptHandler::ExceptHandler(handler) = handler;
                    self.body(&handler.body, in_class);
                }
                self.body(&node.orelse, in_class);
                self.body(&node.finalbody, in_class);
            }
            Stmt::Match(node) => {
                for case in &node.cases {
                    self.body(&case.body, in_class);
                }
            }
            _ => {}
        }
    }

    fn decorators_and_type_params(
        &mut self,
        decorators: &[ruff_python_ast::Decorator],
        parameters: Option<&ruff_python_ast::TypeParams>,
    ) {
        for decorator in decorators {
            self.emit(&decorator.expression);
        }
        for parameter in parameters.into_iter().flat_map(|items| &items.type_params) {
            let (bound, default) = match parameter {
                TypeParam::TypeVar(item) => (item.bound.as_deref(), item.default.as_deref()),
                TypeParam::ParamSpec(item) => (None, item.default.as_deref()),
                TypeParam::TypeVarTuple(item) => (None, item.default.as_deref()),
            };
            for expression in bound.into_iter().chain(default) {
                self.emit(expression);
            }
        }
    }

    fn class(&mut self, node: &StmtClassDef) {
        self.decorators_and_type_params(&node.decorator_list, node.type_params.as_deref());
        for base in node.bases() {
            self.emit(base);
        }
        for keyword in node.keywords() {
            self.emit(&keyword.value);
        }
        // The class body is visited with an empty export set.
        let names = std::mem::take(&mut self.names);
        let mut inner = Api {
            names: HashSet::new(),
        };
        inner.body(&node.body, true);
        let mut found = inner.names;
        self.names = names;
        self.names.extend(found.drain());
        for statement in &node.body {
            for child in nodes_below(TreeNode::Statement(statement)) {
                if let TreeNode::Statement(Stmt::AnnAssign(item)) = child
                    && matches!(item.target.as_ref(), Expr::Attribute(target) if !target.attr.starts_with('_'))
                {
                    self.emit(&item.annotation);
                }
            }
        }
    }

    fn function(&mut self, node: &StmtFunctionDef) {
        self.decorators_and_type_params(&node.decorator_list, node.type_params.as_deref());
        let parameters = &node.parameters;
        for parameter in parameters.iter() {
            if let Some(annotation) = parameter.annotation() {
                self.emit(annotation);
            }
        }
        if let Some(returns) = &node.returns {
            self.emit(returns);
        }
        for parameter in parameters
            .posonlyargs
            .iter()
            .chain(&parameters.args)
            .chain(&parameters.kwonlyargs)
        {
            if let Some(default) = &parameter.default {
                self.emit(default);
            }
        }
    }
}

/// Loaded names, and the names inside strings that parse as expressions.
fn references(root: TreeNode<'_>) -> Vec<String> {
    let mut names = Vec::new();
    for node in nodes_below(root) {
        let TreeNode::Expression(expression) = node else {
            continue;
        };
        match expression {
            Expr::Name(name) if name.ctx == ExprContext::Load => names.push(name.id.to_string()),
            Expr::StringLiteral(text) => names.extend(quoted_names(text.value.to_str())),
            Expr::FString(text) => {
                for part in &text.value {
                    match part {
                        FStringPartRef::Literal(literal) => {
                            names.extend(quoted_names(&literal.value));
                        }
                        FStringPartRef::FString(fstring) => {
                            for element in &fstring.elements {
                                if let InterpolatedStringElement::Literal(literal) = element {
                                    names.extend(quoted_names(&literal.value));
                                }
                            }
                        }
                    }
                }
            }
            _ => {}
        }
    }
    names
}

fn quoted_names(text: &str) -> Vec<String> {
    let Some(parsed) = parse_eval(text) else {
        return Vec::new();
    };
    let mut collector = AllNames { found: Vec::new() };
    collector.visit_expr(parsed.expr());
    collector.found
}

struct AllNames {
    found: Vec<String>,
}

impl<'a> Visitor<'a> for AllNames {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(name) = expression {
            self.found.push(name.id.to_string());
        }
        visitor::walk_expr(self, expression);
    }
}

/// Module namespace reflection, star imports or a re-bound `__all__`.
fn reflective(tree: &ModModule) -> bool {
    let mut finder = Reflection { found: false };
    finder.visit_body(&tree.body);
    finder.found
}

struct Reflection {
    found: bool,
}

impl<'a> Visitor<'a> for Reflection {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let local = |alias: &ruff_python_ast::Alias| {
            alias.asname.as_ref().unwrap_or(&alias.name).as_str() == "__all__"
        };
        self.found |= match statement {
            Stmt::FunctionDef(node) => matches!(node.name.as_str(), "__getattr__" | "__dir__"),
            Stmt::ImportFrom(node) => node
                .names
                .iter()
                .any(|alias| alias.name.as_str() == "*" || local(alias)),
            Stmt::Import(node) => node.names.iter().any(local),
            _ => false,
        };
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(name) = expression
            && REFLECTION.contains(&name.id.as_str())
        {
            self.found = true;
        }
        visitor::walk_expr(self, expression);
    }
}

/// Every binding of a name, like `module_symbols._bindings`.
fn binding_counts(tree: &ModModule) -> HashMap<&str, usize> {
    let mut counter = Bindings {
        counts: HashMap::new(),
    };
    counter.visit_body(&tree.body);
    counter.counts
}

struct Bindings<'a> {
    counts: HashMap<&'a str, usize>,
}

impl<'a> Bindings<'a> {
    fn add(&mut self, name: &'a str) {
        *self.counts.entry(name).or_default() += 1;
    }
}

impl<'a> Visitor<'a> for Bindings<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => self.add(node.name.as_str()),
            Stmt::ClassDef(node) => self.add(node.name.as_str()),
            Stmt::Import(node) => {
                for alias in &node.names {
                    match &alias.asname {
                        Some(local) => self.add(local.as_str()),
                        None => self.add(alias.name.split('.').next().unwrap_or_default()),
                    }
                }
            }
            Stmt::ImportFrom(node) => {
                for alias in &node.names {
                    match &alias.asname {
                        Some(local) => self.add(local.as_str()),
                        None => self.add(alias.name.split('.').next().unwrap_or_default()),
                    }
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
        if let Expr::Name(node) = expression
            && matches!(node.ctx, ExprContext::Store | ExprContext::Del)
        {
            self.add(node.id.as_str());
        }
        visitor::walk_expr(self, expression);
    }

    fn visit_parameter(&mut self, parameter: &'a Parameter) {
        self.add(parameter.name.as_str());
        visitor::walk_parameter(self, parameter);
    }

    fn visit_type_param(&mut self, parameter: &'a TypeParam) {
        self.add(parameter.name().as_str());
        visitor::walk_type_param(self, parameter);
    }

    fn visit_pattern(&mut self, pattern: &'a Pattern) {
        let name = match pattern {
            Pattern::MatchAs(node) => node.name.as_ref(),
            Pattern::MatchStar(node) => node.name.as_ref(),
            Pattern::MatchMapping(node) => node.rest.as_ref(),
            _ => None,
        };
        if let Some(name) = name {
            self.add(name.as_str());
        }
        visitor::walk_pattern(self, pattern);
    }
}
