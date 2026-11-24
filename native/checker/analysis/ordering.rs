//! Stable definition ordering within execution barriers, without running source.
mod blocks;
mod rewrite;

pub use blocks::Owner;
use blocks::{Aliases, Block, Evaluation, UNION_BUILTINS, build_blocks, is_main_guard};
pub use rewrite::rewrite_order;

use super::python_target::PythonTarget;
use super::unparse::{first_line, unparse, unparse_statement_line, unparse_type_param};
use super::walk::{NodeKey, TreeNode, children, expression_key, nodes_below, tree_nodes};
use crate::checker::config::models::{CLASS_ORDER, MODULE_ORDER};
use crate::checker::source::ParsedSource;
use ruff_python_ast::{Expr, ExprContext, Stmt, StmtFunctionDef, TypeParam};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::cmp::Reverse;
use std::collections::{BTreeSet, BinaryHeap, HashMap, HashSet};

const REFLECTIVE_NAMES: [&str; 6] = [
    "globals",
    "locals",
    "vars",
    "eval",
    "exec",
    "__annotations__",
];
const REFLECTIVE_ATTRIBUTES: [&str; 5] = [
    "__dict__",
    "__annotations__",
    "__code__",
    "settrace",
    "setprofile",
];

/// The ordering policy of one package.
pub struct OrderingConfig {
    pub module: Vec<String>,
    pub class: Vec<String>,
    pub constants_exempt: bool,
    pub fix: bool,
}

impl OrderingConfig {
    /// The configured policy, with the defaults of `OrderingConfig` for what it leaves out.
    pub fn new(value: Option<&Value>) -> Self {
        let order = |key: &str, default: &[&str]| {
            value
                .and_then(|value| value.get(key))
                .and_then(Value::as_array)
                .map_or_else(
                    || default.iter().map(|item| (*item).to_owned()).collect(),
                    |items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .map(ToOwned::to_owned)
                            .collect()
                    },
                )
        };
        Self {
            module: order("module", &MODULE_ORDER),
            class: order("class", &CLASS_ORDER),
            constants_exempt: value
                .and_then(|value| value.get("constants_exempt"))
                .and_then(Value::as_bool)
                .unwrap_or(false),
            fix: value
                .and_then(|value| value.get("fix"))
                .and_then(Value::as_str)
                .is_none_or(|fix| fix != "off"),
        }
    }
}

/// A node a finding points at: the statement, expression or type parameter
/// that keeps a section from being fixed.
#[derive(Clone, Copy)]
pub enum Node<'a> {
    Statement(&'a Stmt),
    Expression(&'a Expr),
    TypeParam(&'a TypeParam),
}

impl Node<'_> {
    fn start(self, parsed: &ParsedSource) -> usize {
        usize::from(match self {
            Self::Statement(statement) => parsed.statement_start(statement),
            Self::Expression(expression) => expression.start(),
            Self::TypeParam(parameter) => parameter.start(),
        })
    }

    fn spelling(self, source: &str) -> String {
        match self {
            Self::Statement(statement) => unparse_statement_line(source, statement),
            Self::Expression(expression) => first_line(&unparse(source, expression)).to_owned(),
            Self::TypeParam(parameter) => {
                first_line(&unparse_type_param(source, parameter)).to_owned()
            }
        }
    }
}

#[derive(Clone, Copy)]
struct Blocker<'a> {
    reason: &'static str,
    node: Node<'a>,
}

/// Blocks between two execution barriers of one scope, and their expected order.
pub struct Section<'a> {
    pub owner: Owner<'a>,
    pub scope: String,
    pub blocks: Vec<Block<'a>>,
    pub order: Vec<usize>,
    pub edges: Vec<String>,
    fix_reason: Option<&'static str>,
    fix_blocker: Option<Blocker<'a>>,
}

impl Section<'_> {
    pub fn key(&self) -> String {
        format!(
            "{}:{}:{}",
            self.scope,
            self.blocks[0].line,
            self.blocks[self.blocks.len() - 1].end_line
        )
    }
}

struct Issue<'a> {
    scope: String,
    line: usize,
    message: String,
    edges: Vec<String>,
    section: Option<usize>,
    reason: Option<&'static str>,
    fix_node: Option<Node<'a>>,
}

/// One ordering finding with its `expected` evidence.
pub struct OrderFinding {
    pub message: String,
    pub line: usize,
    pub expected: Value,
}

pub struct FileOrder<'a> {
    contract: String,
    deferred: bool,
    builtin_unions: bool,
    config: OrderingConfig,
    pub sections: Vec<Section<'a>>,
    issues: Vec<Issue<'a>>,
}

/// Analyze the definition order of one module.
pub fn analyze<'a>(
    parsed: &'a ParsedSource,
    config: OrderingConfig,
    contract: String,
    target: &PythonTarget,
) -> FileOrder<'a> {
    let module = parsed.module();
    let future = module.body.iter().any(|statement| {
        matches!(statement, Stmt::ImportFrom(import)
            if import.level == 0
                && import.module.as_ref().is_some_and(|name| name.as_str() == "__future__")
                && import.names.iter().any(|alias| alias.name.as_str() == "annotations"))
    });
    let nodes = tree_nodes(module);
    let mut order = FileOrder {
        contract,
        deferred: future || target.deferred_annotations(),
        builtin_unions: target.minimum.is_some_and(|minimum| minimum >= (3, 10))
            && !mutates_builtins(&nodes),
        config,
        sections: Vec::new(),
        issues: Vec::new(),
    };
    let aliases: Aliases<'a> = UNION_BUILTINS
        .iter()
        .chain(&["property", "staticmethod", "classmethod"])
        .map(|name| (*name, format!("builtins.{name}")))
        .collect();
    let reflection = reflective(&nodes);
    order.body(
        parsed,
        Owner::Module(&module.body),
        "module",
        aliases,
        reflection,
    );
    order
}

impl<'a> FileOrder<'a> {
    /// The findings in the order the analysis met them.
    pub fn findings(&self, parsed: &ParsedSource, path: &str) -> Vec<OrderFinding> {
        self.issues
            .iter()
            .map(|issue| {
                let section = issue.section.map(|index| &self.sections[index]);
                let mut reason = issue
                    .reason
                    .or_else(|| section.and_then(|section| section.fix_reason))
                    .map(ToOwned::to_owned);
                let blocker = issue.fix_node.or_else(|| {
                    section.and_then(|section| section.fix_blocker.map(|blocker| blocker.node))
                });
                let location = blocker.map(|node| {
                    let (line, column) = parsed.text.position(node.start(parsed));
                    let expression = node.spelling(&parsed.text.text);
                    if let Some(reason) = &mut reason {
                        reason.push_str(&format!(
                            " ({path}:{line}:{}: {expression})",
                            column + 1
                        ));
                    }
                    json!({
                        "path": path,
                        "line": line,
                        "column": column + 1,
                        "expression": expression,
                    })
                });
                OrderFinding {
                    message: issue.message.clone(),
                    line: issue.line,
                    expected: json!({
                        "contract": self.contract,
                        "scope": issue.scope,
                        "ordering_edges": issue.edges,
                        "section": section.map(Section::key),
                        "original": section.map(|section| {
                            section.blocks.iter().map(|block| block.label.as_str()).collect::<Vec<_>>()
                        }),
                        "order": section.map(|section| {
                            section
                                .order
                                .iter()
                                .map(|index| section.blocks[*index].label.as_str())
                                .collect::<Vec<_>>()
                        }),
                        "fixable": section.is_some() && reason.is_none(),
                        "fix_reason": reason,
                        "fix_blocker": location,
                        "deferred_annotations": self.deferred,
                    }),
                }
            })
            .collect()
    }

    /// The first section with `key` that has an inversion to fix.
    pub fn fixable_section(&self, key: &str) -> Option<&Section<'a>> {
        self.sections
            .iter()
            .find(|section| !section.edges.is_empty() && section.key() == key)
    }

    fn body(
        &mut self,
        parsed: &'a ParsedSource,
        owner: Owner<'a>,
        scope: &str,
        aliases: Aliases<'a>,
        fix_blocker: Option<Blocker<'a>>,
    ) {
        let evaluation = Evaluation {
            deferred: self.deferred,
            builtin_unions: self.builtin_unions,
        };
        let built = build_blocks(
            parsed,
            owner,
            scope,
            self.config.constants_exempt,
            aliases,
            evaluation,
        );
        let blocks = property_boundaries(built.blocks);
        if !owner.is_class() {
            self.fixed_positions(&blocks);
        }
        let mut group = Vec::new();
        for block in blocks {
            if block.barrier.is_some() {
                self.section(owner, scope, std::mem::take(&mut group), fix_blocker);
            } else {
                group.push(block);
            }
        }
        self.section(owner, scope, group, fix_blocker);
        let mut counts: HashMap<&str, usize> = HashMap::new();
        for (child, child_aliases) in built.children {
            let ordinal = counts.entry(child.name.as_str()).or_default();
            let child_scope = format!("{scope}.{}#{ordinal}", child.name);
            *ordinal += 1;
            let triggers = child
                .decorator_list
                .iter()
                .map(|decorator| Node::Expression(&decorator.expression))
                .chain(child.bases().iter().map(Node::Expression))
                .chain(
                    child
                        .keywords()
                        .iter()
                        .map(|keyword| Node::Expression(&keyword.value)),
                )
                .chain(
                    child
                        .type_params
                        .iter()
                        .flat_map(|parameters| parameters.iter())
                        .map(Node::TypeParam),
                );
            let child_blocker = fix_blocker.or_else(|| {
                earliest(triggers, |node| node.start(parsed)).map(|node| Blocker {
                    reason: "class bases, metaclass or decorators may inspect definition order",
                    node,
                })
            });
            self.body(
                parsed,
                Owner::Class(child),
                &child_scope,
                child_aliases,
                child_blocker,
            );
        }
    }

    fn section(
        &mut self,
        owner: Owner<'a>,
        scope: &str,
        blocks: Vec<Block<'a>>,
        fix_blocker: Option<Blocker<'a>>,
    ) {
        if blocks.is_empty() {
            return;
        }
        let priorities = if owner.is_class() {
            &self.config.class
        } else {
            &self.config.module
        };
        let order = canonical_order(&blocks, priorities);
        let mut positions = vec![0; blocks.len()];
        for (position, index) in order.iter().enumerate() {
            positions[*index] = position;
        }
        let mut edges = Vec::new();
        for left in 0..blocks.len() {
            for right in left + 1..blocks.len() {
                if positions[left] > positions[right] {
                    edges.push(format!("{} -> {}", blocks[left].key, blocks[right].key));
                }
            }
        }
        let mut blocker = fix_blocker.or_else(|| {
            let factory = blocks.iter().find(|block| block.fix_reason.is_some())?;
            Some(Blocker {
                reason: factory.fix_reason?,
                node: factory
                    .fix_node
                    .map_or(Node::Statement(&factory.nodes[0]), Node::Expression),
            })
        });
        let mut reason = blocker.map(|blocker| blocker.reason);
        if !self.config.fix {
            reason = Some("ordering fixes are disabled for this package");
            blocker = None;
        }
        let first_changed = order
            .iter()
            .enumerate()
            .position(|(index, expected)| index != *expected);
        let line = first_changed.map(|index| blocks[index].line);
        let count = edges.len();
        self.sections.push(Section {
            owner,
            scope: scope.to_owned(),
            blocks,
            order,
            edges: edges.clone(),
            fix_reason: reason,
            fix_blocker: blocker,
        });
        if let (false, Some(line)) = (edges.is_empty(), line) {
            self.issues.push(Issue {
                scope: scope.to_owned(),
                line,
                message: format!(
                    "definitions in {scope} have {count} avoidable ordering inversion(s)"
                ),
                edges,
                section: Some(self.sections.len() - 1),
                reason: None,
                fix_node: None,
            });
        }
    }

    fn fixed_positions(&mut self, blocks: &[Block<'a>]) {
        let ranks: HashMap<&str, usize> = self
            .config
            .module
            .iter()
            .enumerate()
            .map(|(index, name)| (name.as_str(), index))
            .collect();
        for (index, block) in blocks.iter().enumerate() {
            if matches!(block.category, "imports" | "future-imports") {
                let rank = ranks.get(block.category).copied().unwrap_or_default();
                let edges: Vec<String> = blocks[..index]
                    .iter()
                    .filter(|prior| ranks.get(prior.category).is_some_and(|other| *other > rank))
                    .map(|prior| format!("{} -> fixed:{}", prior.key, block.key))
                    .collect();
                if !edges.is_empty() {
                    self.issues.push(Issue {
                        scope: "module".to_owned(),
                        line: block.line,
                        message: format!("{} appear after later definition groups", block.category),
                        edges,
                        section: None,
                        reason: Some("imports are execution barriers; review placement manually"),
                        fix_node: Some(Node::Statement(&block.nodes[0])),
                    });
                }
            }
            if is_main_guard(&block.nodes[0]) && index + 1 < blocks.len() {
                self.issues.push(Issue {
                    scope: "module".to_owned(),
                    line: block.line,
                    message: "the main guard must be the final module statement".to_owned(),
                    edges: blocks[index + 1..]
                        .iter()
                        .map(|later| format!("main:{} -> {}", block.key, later.key))
                        .collect(),
                    section: None,
                    reason: Some("moving the main guard changes execution timing; review manually"),
                    fix_node: Some(Node::Statement(&block.nodes[0])),
                });
            }
        }
    }
}

/// The first of the earliest nodes, as Python's `min` picks it.
fn earliest<T: Copy>(items: impl IntoIterator<Item = T>, start: impl Fn(T) -> usize) -> Option<T> {
    let mut best: Option<(usize, T)> = None;
    for item in items {
        let position = start(item);
        if best.is_none_or(|(current, _)| position < current) {
            best = Some((position, item));
        }
    }
    best.map(|(_, item)| item)
}

fn canonical_order(blocks: &[Block<'_>], priorities: &[String]) -> Vec<usize> {
    let mut dependencies = precedence(blocks);
    let ranks: HashMap<&str, usize> = priorities
        .iter()
        .enumerate()
        .map(|(index, name)| (name.as_str(), index))
        .collect();
    let mut followers: Vec<Vec<usize>> = vec![Vec::new(); blocks.len()];
    for (index, required) in dependencies.iter().enumerate() {
        for prerequisite in required {
            followers[*prerequisite].push(index);
        }
    }
    let mut priority: Vec<usize> = blocks
        .iter()
        .map(|block| ranks.get(block.category).copied().unwrap_or(ranks.len()))
        .collect();
    for index in (0..blocks.len()).rev() {
        for follower in &followers[index] {
            priority[index] = priority[index].min(priority[*follower]);
        }
    }
    let mut ready: BinaryHeap<Reverse<(usize, usize)>> = (0..blocks.len())
        .filter(|index| dependencies[*index].is_empty())
        .map(|index| Reverse((priority[index], index)))
        .collect();
    let mut result = Vec::with_capacity(blocks.len());
    while let Some(Reverse((_, index))) = ready.pop() {
        result.push(index);
        for follower in &followers[index] {
            dependencies[*follower].remove(&index);
            if dependencies[*follower].is_empty() {
                ready.push(Reverse((priority[*follower], *follower)));
            }
        }
    }
    result
}

/// Preserve group order, binding versions, and reads before later rebinding.
fn precedence(blocks: &[Block<'_>]) -> Vec<BTreeSet<usize>> {
    let mut before: Vec<BTreeSet<usize>> = vec![BTreeSet::new(); blocks.len()];
    let mut last_group: HashMap<&str, usize> = HashMap::new();
    let mut writers: HashMap<&str, Vec<usize>> = HashMap::new();
    for (index, block) in blocks.iter().enumerate() {
        if let Some(previous) = last_group.insert(block.category, index) {
            before[index].insert(previous);
        }
        for name in &block.binds {
            let written = writers.entry(name).or_default();
            if let Some(previous) = written.last() {
                before[index].insert(*previous);
            }
            written.push(index);
        }
    }
    for (index, block) in blocks.iter().enumerate() {
        for name in &block.reads {
            let Some(written) = writers.get(name) else {
                continue;
            };
            if let Some(prior) = written.iter().rev().find(|writer| **writer < index) {
                before[index].insert(*prior);
            }
            if let Some(later) = written.iter().find(|writer| **writer > index) {
                before[*later].insert(index);
            }
        }
    }
    before
}

fn property_boundaries(mut blocks: Vec<Block<'_>>) -> Vec<Block<'_>> {
    let families: HashSet<String> = blocks
        .iter()
        .filter(|block| {
            block.nodes.iter().any(|node| {
                matches!(node, Stmt::FunctionDef(function) if function.decorator_list.iter().any(|decorator| {
                    matches!(&decorator.expression, Expr::Attribute(attribute)
                        if matches!(attribute.attr.as_str(), "getter" | "setter" | "deleter"))
                }))
            })
        })
        .map(|block| block.label.clone())
        .collect();
    for name in families {
        let indices: Vec<usize> = (0..blocks.len())
            .filter(|index| blocks[*index].label == name)
            .collect();
        if indices.len() > 1 {
            for index in indices {
                blocks[index].barrier = Some("noncontiguous property family is kept in place");
            }
        }
    }
    blocks
}

fn reflective<'a>(nodes: &[TreeNode<'a>]) -> Option<Blocker<'a>> {
    let mut found: Vec<TreeNode<'a>> = nodes
        .iter()
        .copied()
        .filter(|node| match node {
            TreeNode::Statement(Stmt::Global(_)) => true,
            TreeNode::Expression(Expr::Name(name)) => REFLECTIVE_NAMES.contains(&name.id.as_str()),
            TreeNode::Expression(Expr::Attribute(attribute)) => {
                REFLECTIVE_ATTRIBUTES.contains(&attribute.attr.as_str())
            }
            _ => false,
        })
        .collect();
    // Only these two forms can refer to a nested function's own namespace.
    let namespaces = found.iter().any(|node| match node {
        TreeNode::Expression(Expr::Name(name)) => name.id.as_str() == "vars",
        TreeNode::Expression(Expr::Attribute(attribute)) => attribute.attr.as_str() == "__dict__",
        _ => false,
    });
    if namespaces {
        let unrelated = local_function_namespaces(nodes);
        found.retain(|node| !unrelated.contains(&node.key()));
    }
    let node = earliest(found, |node| match node {
        TreeNode::Statement(statement) => usize::from(statement.start()),
        TreeNode::Expression(expression) => usize::from(expression.start()),
        _ => usize::MAX,
    })?;
    Some(Blocker {
        reason: "runtime namespace or annotation introspection requires manual review",
        node: match node {
            TreeNode::Statement(statement) => Node::Statement(statement),
            TreeNode::Expression(expression) => Node::Expression(expression),
            _ => return None,
        },
    })
}

/// Uses of a nested function's attribute dictionary, which is not the ordered namespace.
fn local_function_namespaces(nodes: &[TreeNode<'_>]) -> HashSet<NodeKey> {
    let mut safe = HashSet::new();
    let shadowed_vars = nodes.iter().any(|node| match node {
        TreeNode::Expression(Expr::Name(name)) => {
            matches!(name.ctx, ExprContext::Store | ExprContext::Del) && name.id.as_str() == "vars"
        }
        TreeNode::Parameter(parameter) => parameter.name.as_str() == "vars",
        TreeNode::Statement(Stmt::FunctionDef(function)) => function.name.as_str() == "vars",
        TreeNode::Statement(Stmt::ClassDef(class)) => class.name.as_str() == "vars",
        TreeNode::Statement(statement) => import_names(statement)
            .into_iter()
            .any(|(local, name)| local == "vars" || name == "*"),
        _ => false,
    });
    for node in nodes {
        let TreeNode::Statement(statement @ Stmt::FunctionDef(function)) = node else {
            continue;
        };
        let nested: Vec<&StmtFunctionDef> = function
            .body
            .iter()
            .filter_map(|child| match child {
                Stmt::FunctionDef(inner) if inner.decorator_list.is_empty() => Some(inner),
                _ => None,
            })
            .collect();
        if nested.is_empty() {
            continue;
        }
        let mut unavailable: HashSet<&str> =
            nodes_below(TreeNode::Parameters(&function.parameters))
                .into_iter()
                .filter_map(|node| match node {
                    TreeNode::Parameter(parameter) => Some(parameter.name.as_str()),
                    _ => None,
                })
                .collect();
        for inner in nodes_below(TreeNode::Statement(statement)) {
            let TreeNode::Statement(inner) = inner else {
                continue;
            };
            match inner {
                Stmt::Global(declaration) => {
                    unavailable.extend(declaration.names.iter().map(|name| name.as_str()));
                }
                Stmt::Nonlocal(declaration) => {
                    unavailable.extend(declaration.names.iter().map(|name| name.as_str()));
                }
                _ => unavailable.extend(import_names(inner).into_iter().map(|(local, _)| local)),
            }
        }
        let local: HashSet<&str> = nested
            .iter()
            .map(|inner| inner.name.as_str())
            .filter(|name| binding_count(&function.body, name) == 1 && !unavailable.contains(name))
            .collect();
        if local.is_empty() {
            continue;
        }
        for node in deferred_body_nodes(&function.body) {
            let TreeNode::Expression(expression) = node else {
                continue;
            };
            match expression {
                Expr::Attribute(attribute)
                    if attribute.attr.as_str() == "__dict__"
                        && matches!(attribute.value.as_ref(), Expr::Name(name) if local.contains(name.id.as_str())) =>
                {
                    safe.insert(expression_key(expression));
                }
                Expr::Call(call)
                    if !shadowed_vars
                        && matches!(call.func.as_ref(), Expr::Name(name) if name.id.as_str() == "vars")
                        && call.arguments.args.len() == 1
                        && call.arguments.keywords.is_empty()
                        && matches!(&call.arguments.args[0], Expr::Name(name) if local.contains(name.id.as_str())) =>
                {
                    safe.insert(expression_key(&call.func));
                }
                _ => {}
            }
        }
    }
    safe
}

/// The local names an import binds, with the imported names; `*` for a star import.
fn import_names(statement: &Stmt) -> Vec<(&str, &str)> {
    let names = match statement {
        Stmt::Import(import) => &import.names,
        Stmt::ImportFrom(import) => &import.names,
        _ => return Vec::new(),
    };
    names
        .iter()
        .map(|alias| {
            let local = alias.asname.as_ref().map_or_else(
                || alias.name.split('.').next().unwrap_or_default(),
                |name| name.as_str(),
            );
            (local, alias.name.as_str())
        })
        .collect()
}

/// How often `name` is bound anywhere below `body`, as `definition_bindings` counts.
fn binding_count(body: &[Stmt], name: &str) -> usize {
    body.iter()
        .flat_map(|statement| nodes_below(TreeNode::Statement(statement)))
        .filter(|node| match node {
            TreeNode::Expression(Expr::Name(found)) => {
                matches!(found.ctx, ExprContext::Store | ExprContext::Del)
                    && found.id.as_str() == name
            }
            TreeNode::Statement(Stmt::FunctionDef(function)) => function.name.as_str() == name,
            TreeNode::Statement(Stmt::ClassDef(class)) => class.name.as_str() == name,
            _ => false,
        })
        .count()
}

/// Nodes a body evaluates when it runs, without nested function or class bodies.
fn deferred_body_nodes(body: &[Stmt]) -> Vec<TreeNode<'_>> {
    let mut nodes = Vec::new();
    let mut pending: Vec<TreeNode<'_>> = body.iter().map(TreeNode::Statement).collect();
    while let Some(node) = pending.pop() {
        nodes.push(node);
        if !matches!(
            node,
            TreeNode::Statement(Stmt::FunctionDef(_) | Stmt::ClassDef(_))
                | TreeNode::Expression(Expr::Lambda(_))
        ) {
            pending.extend(children(node));
        }
    }
    nodes
}

fn mutates_builtins(nodes: &[TreeNode<'_>]) -> bool {
    let mut modules: HashSet<&str> = HashSet::from(["builtins"]);
    for node in nodes {
        if let TreeNode::Statement(Stmt::Import(import)) = node {
            modules.extend(
                import
                    .names
                    .iter()
                    .filter(|alias| alias.name.as_str() == "builtins")
                    .map(|alias| alias.asname.as_ref().unwrap_or(&alias.name).as_str()),
            );
        }
    }
    nodes.iter().any(|node| match node {
        TreeNode::Statement(Stmt::Global(_)) => true,
        TreeNode::Expression(Expr::Attribute(attribute)) => {
            matches!(attribute.ctx, ExprContext::Store | ExprContext::Del)
                && matches!(attribute.value.as_ref(), Expr::Name(name) if modules.contains(name.id.as_str()))
        }
        _ => false,
    })
}
