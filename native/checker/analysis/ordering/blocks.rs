//! Statements that can be considered together without executing source code.
use crate::checker::analysis::nodes::is_constant;
use crate::checker::analysis::unparse::unparse_statement_line;
use crate::checker::analysis::walk::{TreeNode, nodes_below};
use crate::checker::source::ParsedSource;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    CmpOp, Expr, ExprContext, Operator, Parameters, Stmt, StmtClassDef, StmtFunctionDef,
};
use ruff_text_size::Ranged;
use std::collections::{HashMap, HashSet};

/// Local names and the qualified names they are known to stand for.
pub type Aliases<'a> = HashMap<&'a str, String>;

const TYPE_FACTORIES: [&str; 6] = [
    "typing.TypeVar",
    "typing.TypeVarTuple",
    "typing.ParamSpec",
    "typing_extensions.TypeVar",
    "typing_extensions.TypeVarTuple",
    "typing_extensions.ParamSpec",
];
const OVERLOAD: [&str; 2] = ["typing.overload", "typing_extensions.overload"];
const WRAPPERS: [&str; 3] = [
    "builtins.property",
    "builtins.staticmethod",
    "builtins.classmethod",
];
const INIT: [&str; 3] = ["__init__", "__new__", "__post_init__"];
const PROPERTY_INSTANCE: &str = "builtins.property-instance";
const ACCESSORS: [&str; 3] = ["getter", "setter", "deleter"];

/// Builtin types whose unions are modeled in eager annotations.
pub const UNION_BUILTINS: [&str; 17] = [
    "bool",
    "bytearray",
    "bytes",
    "complex",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "memoryview",
    "object",
    "range",
    "set",
    "slice",
    "str",
    "tuple",
    "type",
];

/// The namespace whose body is ordered: the module or one class.
#[derive(Clone, Copy)]
pub enum Owner<'a> {
    Module(&'a [Stmt]),
    Class(&'a StmtClassDef),
}

impl<'a> Owner<'a> {
    pub fn body(self) -> &'a [Stmt] {
        match self {
            Self::Module(body) => body,
            Self::Class(class) => &class.body,
        }
    }

    pub fn is_class(self) -> bool {
        matches!(self, Self::Class(_))
    }
}

/// How the target Python evaluates annotations.
#[derive(Clone, Copy)]
pub struct Evaluation {
    pub deferred: bool,
    pub builtin_unions: bool,
}

/// One statement, or an overload family, with what it binds and reads.
#[derive(Clone)]
pub struct Block<'a> {
    pub nodes: &'a [Stmt],
    pub first: usize,
    pub stop: usize,
    pub label: String,
    pub key: String,
    pub category: &'static str,
    pub binds: HashSet<&'a str>,
    pub reads: HashSet<&'a str>,
    pub barrier: Option<&'static str>,
    pub fix_reason: Option<&'static str>,
    pub fix_node: Option<&'a Expr>,
    pub line: usize,
    pub end_line: usize,
}

/// The blocks of one body and the classes defined directly in it.
pub struct Built<'a> {
    pub blocks: Vec<Block<'a>>,
    pub children: Vec<(&'a StmtClassDef, Aliases<'a>)>,
}

pub fn build_blocks<'a>(
    parsed: &'a ParsedSource,
    owner: Owner<'a>,
    scope: &str,
    constants_exempt: bool,
    mut aliases: Aliases<'a>,
    evaluation: Evaluation,
) -> Built<'a> {
    let text = &parsed.text;
    let line = |statement: &Stmt| text.line_at(usize::from(statement.start()));
    let end_line = |statement: &Stmt| text.line_at(usize::from(statement.end()));
    let body = owner.body();
    let in_class = owner.is_class();
    let mut blocks = Vec::new();
    let mut children = Vec::new();
    let mut counts: HashMap<String, usize> = HashMap::new();
    let mut index = 0;
    while index < body.len() {
        let first = index;
        let node = &body[index];
        index += family(body, first) - 1;
        let group = &body[first..=index];
        if let Stmt::ClassDef(class) = node {
            children.push((class, aliases.clone()));
        }
        let category = if first == 0 && is_string_statement(node) {
            "docstring"
        } else {
            category(node, in_class, constants_exempt, &aliases)
        };
        let label = match node {
            Stmt::FunctionDef(function) => function.name.to_string(),
            Stmt::ClassDef(class) => class.name.to_string(),
            _ => assignment_name(node).map_or_else(
                || unparse_statement_line(&text.text, node),
                ToOwned::to_owned,
            ),
        };
        let count = counts.entry(label.clone()).or_default();
        let key = format!("{scope}.{label}#{count}");
        *count += 1;
        let mut binds = HashSet::new();
        let mut reads = HashSet::new();
        let mut reason = None;
        let mut member_aliases = aliases.clone();
        for member in group {
            for name in definition_reads(member, evaluation.deferred) {
                if !binds.contains(name) {
                    reads.insert(name);
                }
            }
            binds.extend(bound_names(member));
            if reason.is_none() {
                reason = barrier(member, &member_aliases, evaluation);
            }
            let property = match member {
                Stmt::FunctionDef(function) => Some(function).filter(|function| {
                    function.decorator_list.iter().any(|decorator| {
                        qualified_name(&decorator.expression, &member_aliases).as_deref()
                            == Some("builtins.property")
                            || matches!(&decorator.expression, Expr::Attribute(attribute)
                                if matches!(attribute.value.as_ref(), Expr::Name(name)
                                    if name.id.as_str() == function.name.as_str())
                                && member_aliases.get(function.name.as_str()).map(String::as_str)
                                    == Some(PROPERTY_INSTANCE))
                    })
                }),
                _ => None,
            };
            update_aliases(member, &mut member_aliases);
            if let Some(function) = property {
                member_aliases.insert(function.name.as_str(), PROPERTY_INSTANCE.to_owned());
            }
        }
        if in_class && matches!(node, Stmt::ClassDef(_)) {
            reason = Some("nested class creation is an execution boundary");
        }
        if category == "docstring" {
            reason = Some("docstring position is fixed");
        }
        let last = &body[index];
        if (first > 0 && end_line(&body[first - 1]) >= line(node))
            || body
                .get(index + 1)
                .is_some_and(|next| end_line(last) >= line(next))
        {
            reason = Some("statements share a physical line");
        }
        let fix_node = factory_fix_node(node, &aliases);
        blocks.push(Block {
            nodes: group,
            first,
            stop: index + 1,
            label,
            key,
            category,
            binds,
            reads,
            barrier: reason,
            fix_reason: fix_node.map(|_| "nonliteral type-variable construction requires review"),
            fix_node,
            line: group.iter().map(line).min().unwrap_or_default(),
            end_line: group.iter().map(end_line).max().unwrap_or_default(),
        });
        aliases = member_aliases;
        index += 1;
    }
    Built { blocks, children }
}

pub fn is_main_guard(statement: &Stmt) -> bool {
    let Stmt::If(node) = statement else {
        return false;
    };
    let Expr::Compare(test) = node.test.as_ref() else {
        return false;
    };
    if test.ops.len() != 1 || test.ops[0] != CmpOp::Eq {
        return false;
    }
    let [left, right] = &*test.operands else {
        return false;
    };
    let dunder_name =
        |value: &Expr| matches!(value, Expr::Name(name) if name.id.as_str() == "__name__");
    let main = |value: &Expr| matches!(value, Expr::StringLiteral(literal) if literal.value.to_str() == "__main__");
    (dunder_name(left) && main(right)) || (dunder_name(right) && main(left))
}

/// A bare string statement, which is a docstring in first position.
fn is_string_statement(statement: &Stmt) -> bool {
    matches!(statement, Stmt::Expr(node) if node.value.is_string_literal_expr())
}

/// The function and its directly following redefinitions, as one family.
fn family(body: &[Stmt], first: usize) -> usize {
    let Stmt::FunctionDef(function) = &body[first] else {
        return 1;
    };
    1 + body[first + 1..]
        .iter()
        .take_while(|following| {
            matches!(following, Stmt::FunctionDef(next) if next.name.as_str() == function.name.as_str())
        })
        .count()
}

fn class_barrier(
    class: &StmtClassDef,
    aliases: &Aliases<'_>,
    evaluation: Evaluation,
) -> Option<&'static str> {
    let has_arguments = class
        .arguments
        .as_deref()
        .is_some_and(|arguments| !arguments.is_empty());
    if has_arguments || !class.decorator_list.is_empty() || class.type_params.is_some() {
        return Some("class creation may execute bases, decorators or metaclass behavior");
    }
    let mut class_aliases = aliases.clone();
    for child in &class.body {
        if matches!(child, Stmt::ClassDef(_)) {
            return Some("nested class creation is an execution boundary");
        }
        if matches!(child, Stmt::Pass(_)) || is_string_statement(child) {
            continue;
        }
        if barrier(child, &class_aliases, evaluation).is_some() {
            return Some("class body contains an execution boundary");
        }
        update_aliases(child, &mut class_aliases);
    }
    None
}

fn update_aliases<'a>(node: &'a Stmt, aliases: &mut Aliases<'a>) {
    for name in bound_names(node) {
        aliases.remove(name);
    }
    if namespace_mutation(node) {
        aliases.clear();
        return;
    }
    for expression in eager_expressions(node, false) {
        for child in nodes_below(TreeNode::Expression(expression)) {
            if let TreeNode::Expression(Expr::Named(named)) = child
                && let Expr::Name(target) = named.target.as_ref()
            {
                aliases.remove(target.id.as_str());
            }
        }
    }
    match node {
        Stmt::ImportFrom(import) if import.names.iter().any(|alias| alias.name.as_str() == "*") => {
            aliases.clear();
        }
        Stmt::Import(import) => {
            for alias in &import.names {
                let head = alias.name.split('.').next().unwrap_or_default();
                let local = alias.asname.as_ref().map_or(head, |name| name.as_str());
                let target = if alias.asname.is_some() {
                    alias.name.as_str()
                } else {
                    head
                };
                aliases.insert(local, target.to_owned());
            }
        }
        Stmt::ImportFrom(import) if import.level == 0 => {
            let module = import.module.as_ref().map_or("None", |name| name.as_str());
            for alias in &import.names {
                let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                aliases.insert(local, format!("{module}.{}", alias.name.as_str()));
            }
        }
        Stmt::If(_) | Stmt::Try(_) | Stmt::While(_) | Stmt::Match(_) => {
            forget_conditional_aliases(node, aliases);
        }
        Stmt::With(item) if !item.is_async => forget_conditional_aliases(node, aliases),
        Stmt::For(item) if !item.is_async => forget_conditional_aliases(node, aliases),
        _ => {}
    }
}

fn forget_conditional_aliases<'a>(node: &'a Stmt, aliases: &mut Aliases<'a>) {
    for child in nodes_below(TreeNode::Statement(node)) {
        match child {
            TreeNode::Statement(statement) => {
                for name in bound_names(statement) {
                    aliases.remove(name);
                }
                if let Stmt::ImportFrom(import) = statement
                    && import.names.iter().any(|alias| alias.name.as_str() == "*")
                {
                    aliases.clear();
                }
            }
            TreeNode::Expression(Expr::Name(name))
                if matches!(name.ctx, ExprContext::Store | ExprContext::Del) =>
            {
                aliases.remove(name.id.as_str());
            }
            _ => {}
        }
    }
}

fn definition_reads(node: &Stmt, deferred: bool) -> HashSet<&str> {
    let mut reads = Reads::default();
    for expression in eager_expressions(node, deferred) {
        reads.visit_expr(expression);
    }
    let mut names = reads.names;
    if let Stmt::ClassDef(class) = node {
        let mut local = HashSet::new();
        for child in &class.body {
            names.extend(
                definition_reads(child, deferred)
                    .into_iter()
                    .filter(|name| !local.contains(name)),
            );
            local.extend(bound_names(child));
        }
    }
    names
}

/// The names a statement binds in its own namespace.
pub fn bound_names(node: &Stmt) -> Vec<&str> {
    match node {
        Stmt::FunctionDef(function) => vec![function.name.as_str()],
        Stmt::ClassDef(class) => vec![class.name.as_str()],
        Stmt::Assign(assign) => assign
            .targets
            .iter()
            .flat_map(|target| target_names(target, true))
            .collect(),
        Stmt::AnnAssign(assign) if assign.value.is_some() => match assign.target.as_ref() {
            Expr::Name(name) => vec![name.id.as_str()],
            _ => Vec::new(),
        },
        Stmt::Import(import) => import
            .names
            .iter()
            .map(|alias| {
                alias.asname.as_ref().map_or_else(
                    || alias.name.split('.').next().unwrap_or_default(),
                    |name| name.as_str(),
                )
            })
            .collect(),
        Stmt::ImportFrom(import) => import
            .names
            .iter()
            .filter(|alias| alias.name.as_str() != "*")
            .map(|alias| alias.asname.as_ref().unwrap_or(&alias.name).as_str())
            .collect(),
        Stmt::AugAssign(assign) => match assign.target.as_ref() {
            Expr::Name(name) => vec![name.id.as_str()],
            _ => Vec::new(),
        },
        Stmt::TypeAlias(alias) => match alias.name.as_ref() {
            Expr::Name(name) => vec![name.id.as_str()],
            _ => Vec::new(),
        },
        Stmt::Delete(delete) => delete
            .targets
            .iter()
            .flat_map(|target| target_names(target, false))
            .collect(),
        _ => Vec::new(),
    }
}

/// The names below a target, only stored ones when `stored` is set.
fn target_names(target: &Expr, stored: bool) -> Vec<&str> {
    nodes_below(TreeNode::Expression(target))
        .into_iter()
        .filter_map(|node| match node {
            TreeNode::Expression(Expr::Name(name)) if !stored || name.ctx == ExprContext::Store => {
                Some(name.id.as_str())
            }
            _ => None,
        })
        .collect()
}

fn namespace_mutation(node: &Stmt) -> bool {
    let targets: Vec<&Expr> = match node {
        Stmt::Assign(assign) => assign.targets.iter().collect(),
        Stmt::Delete(delete) => delete.targets.iter().collect(),
        Stmt::AnnAssign(assign) => vec![&assign.target],
        Stmt::AugAssign(assign) => vec![&assign.target],
        _ => Vec::new(),
    };
    targets.into_iter().any(|target| {
        nodes_below(TreeNode::Expression(target))
            .into_iter()
            .any(|node| {
                matches!(
                    node,
                    TreeNode::Expression(Expr::Attribute(_) | Expr::Subscript(_))
                )
            })
    })
}

fn wrapper(decorator: &Expr, function: &StmtFunctionDef, aliases: &Aliases<'_>) -> bool {
    if qualified_name(decorator, aliases)
        .is_some_and(|name| OVERLOAD.contains(&name.as_str()) || WRAPPERS.contains(&name.as_str()))
    {
        return true;
    }
    matches!(decorator, Expr::Attribute(attribute)
        if matches!(attribute.value.as_ref(), Expr::Name(name) if name.id.as_str() == function.name.as_str())
        && aliases.get(function.name.as_str()).map(String::as_str) == Some(PROPERTY_INSTANCE)
        && ACCESSORS.contains(&attribute.attr.as_str()))
}

fn barrier(node: &Stmt, aliases: &Aliases<'_>, evaluation: Evaluation) -> Option<&'static str> {
    match node {
        Stmt::FunctionDef(function) => safe_function(function, aliases, evaluation),
        Stmt::ClassDef(class) => class_barrier(class, aliases, evaluation),
        Stmt::Assign(_) | Stmt::AnnAssign(_) => {
            if assignment_name(node).is_none() {
                return Some("mutation or multiple assignment targets");
            }
            if type_factory(node, aliases)
                && let Some(Expr::Call(call)) = assigned_value(node)
            {
                let safe =
                    call.arguments.args.iter().all(pure_expression)
                        && call.arguments.keywords.iter().all(|keyword| {
                            keyword.arg.is_some() && pure_expression(&keyword.value)
                        });
                return (!safe).then_some("type-variable arguments may execute code");
            }
            let pure = eager_expressions(node, evaluation.deferred)
                .into_iter()
                .all(pure_expression);
            (!pure).then_some("assignment may execute calls, operators or protocols")
        }
        _ => Some("import, control flow, registration or other executable statement"),
    }
}

fn category(
    node: &Stmt,
    in_class: bool,
    constants_exempt: bool,
    aliases: &Aliases<'_>,
) -> &'static str {
    match node {
        Stmt::ImportFrom(import)
            if import.level == 0
                && import
                    .module
                    .as_ref()
                    .is_some_and(|module| module.as_str() == "__future__") =>
        {
            "future-imports"
        }
        Stmt::Import(_) | Stmt::ImportFrom(_) => "imports",
        _ if is_main_guard(node) => "main-guard",
        Stmt::Assign(_) | Stmt::AnnAssign(_) => {
            let name = assignment_name(node);
            if in_class {
                "class-attributes"
            } else if name.is_some_and(is_dunder) {
                "dunder-assignments"
            } else if type_factory(node, aliases) {
                "type-vars"
            } else if constants_exempt
                && name.is_some_and(|name| {
                    crate::checker::analysis::nodes::is_upper(name.trim_start_matches('_'))
                })
            {
                "public"
            } else {
                visibility(name.unwrap_or("_"))
            }
        }
        Stmt::FunctionDef(function) => {
            let name = function.name.as_str();
            if in_class && INIT.contains(&name) {
                "init"
            } else if in_class && is_dunder(name) {
                "dunders"
            } else {
                visibility(name)
            }
        }
        Stmt::ClassDef(class) => visibility(class.name.as_str()),
        _ => "barrier",
    }
}

fn factory_fix_node<'a>(node: &'a Stmt, aliases: &Aliases<'_>) -> Option<&'a Expr> {
    if !type_factory(node, aliases) {
        return None;
    }
    let Some(Expr::Call(call)) = assigned_value(node) else {
        return None;
    };
    call.arguments
        .args
        .iter()
        .chain(call.arguments.keywords.iter().map(|keyword| &keyword.value))
        .find(|value| !is_constant(value))
}

fn assigned_value(node: &Stmt) -> Option<&Expr> {
    match node {
        Stmt::Assign(assign) => Some(&assign.value),
        Stmt::AnnAssign(assign) => assign.value.as_deref(),
        _ => None,
    }
}

fn type_factory(node: &Stmt, aliases: &Aliases<'_>) -> bool {
    matches!(assigned_value(node), Some(Expr::Call(call))
        if qualified_name(&call.func, aliases).is_some_and(|name| TYPE_FACTORIES.contains(&name.as_str())))
}

/// The dotted name an expression is known to stand for.
pub fn qualified_name(node: &Expr, aliases: &Aliases<'_>) -> Option<String> {
    match node {
        Expr::Name(name) => aliases.get(name.id.as_str()).cloned(),
        Expr::Attribute(attribute) => qualified_name(&attribute.value, aliases)
            .map(|parent| format!("{parent}.{}", attribute.attr.as_str())),
        _ => None,
    }
}

fn safe_function(
    function: &StmtFunctionDef,
    aliases: &Aliases<'_>,
    evaluation: Evaluation,
) -> Option<&'static str> {
    if function.type_params.is_some() {
        return Some("generic type parameters are an execution boundary");
    }
    if !function
        .decorator_list
        .iter()
        .all(|decorator| wrapper(&decorator.expression, function, aliases))
    {
        return Some("unrecognized decorators execute at definition time");
    }
    let annotations = if evaluation.deferred {
        Vec::new()
    } else {
        annotations(&function.parameters, function.returns.as_deref())
    };
    let safe = defaults(&function.parameters).all(pure_expression)
        && annotations.into_iter().all(|value| {
            pure_expression(value) || (evaluation.builtin_unions && builtin_union(value, aliases))
        });
    (!safe).then_some("default or eager annotation evaluation may execute code")
}

fn defaults(parameters: &Parameters) -> impl Iterator<Item = &Expr> {
    parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .chain(&parameters.kwonlyargs)
        .filter_map(|parameter| parameter.default.as_deref())
}

fn eager_expressions(node: &Stmt, deferred: bool) -> Vec<&Expr> {
    match node {
        Stmt::FunctionDef(function) => {
            let mut found: Vec<&Expr> = function
                .decorator_list
                .iter()
                .map(|decorator| &decorator.expression)
                .collect();
            found.extend(defaults(&function.parameters));
            if !deferred {
                found.extend(annotations(
                    &function.parameters,
                    function.returns.as_deref(),
                ));
            }
            found
        }
        Stmt::ClassDef(class) => {
            let mut found: Vec<&Expr> = class
                .decorator_list
                .iter()
                .map(|decorator| &decorator.expression)
                .collect();
            found.extend(class.bases());
            found.extend(class.keywords().iter().map(|keyword| &keyword.value));
            found
        }
        Stmt::Assign(assign) => vec![&assign.value],
        Stmt::AnnAssign(assign) => {
            let mut found: Vec<&Expr> = assign.value.as_deref().into_iter().collect();
            if !deferred {
                found.push(&assign.annotation);
            }
            found
        }
        _ => Vec::new(),
    }
}

fn annotations<'a>(parameters: &'a Parameters, returns: Option<&'a Expr>) -> Vec<&'a Expr> {
    parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .chain(&parameters.kwonlyargs)
        .map(|parameter| &parameter.parameter)
        .chain(parameters.vararg.as_deref())
        .chain(parameters.kwarg.as_deref())
        .filter_map(|parameter| parameter.annotation.as_deref())
        .chain(returns)
        .collect()
}

/// Loaded names, outside lambda bodies, that evaluating an expression reads.
#[derive(Default)]
struct Reads<'a> {
    names: HashSet<&'a str>,
}

impl<'a> Visitor<'a> for Reads<'a> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Name(name) => {
                if name.ctx == ExprContext::Load {
                    self.names.insert(name.id.as_str());
                }
            }
            Expr::Lambda(lambda) => {
                if let Some(parameters) = &lambda.parameters {
                    for value in defaults(parameters) {
                        self.visit_expr(value);
                    }
                }
            }
            _ => visitor::walk_expr(self, expression),
        }
    }
}

fn pure_expression(node: &Expr) -> bool {
    match node {
        Expr::Name(_) => true,
        _ if is_constant(node) => true,
        Expr::Tuple(tuple) => tuple.elts.iter().all(pure_expression),
        Expr::List(list) => list.elts.iter().all(pure_expression),
        Expr::Dict(dict) => dict
            .items
            .iter()
            .all(|item| item.key.as_ref().is_some_and(is_constant) && pure_expression(&item.value)),
        Expr::Set(set) => set.elts.iter().all(is_constant),
        Expr::Lambda(lambda) => lambda
            .parameters
            .as_deref()
            .is_none_or(|parameters| defaults(parameters).all(pure_expression)),
        _ => false,
    }
}

fn builtin_union(node: &Expr, aliases: &Aliases<'_>) -> bool {
    let Expr::BinOp(operation) = node else {
        return false;
    };
    if operation.op != Operator::BitOr {
        return false;
    }
    let operand = |value: &Expr| {
        qualified_name(value, aliases).is_some_and(|name| {
            name.strip_prefix("builtins.")
                .is_some_and(|rest| UNION_BUILTINS.contains(&rest))
        }) || value.is_none_literal_expr()
            || builtin_union(value, aliases)
    };
    operand(&operation.left)
        && operand(&operation.right)
        && !(operation.left.is_none_literal_expr() && operation.right.is_none_literal_expr())
}

fn is_dunder(name: &str) -> bool {
    name.starts_with("__") && name.ends_with("__")
}

fn visibility(name: &str) -> &'static str {
    if name.starts_with('_') && !is_dunder(name) {
        "private"
    } else {
        "public"
    }
}

/// The single name an assignment binds, if it has one plain target.
fn assignment_name(node: &Stmt) -> Option<&str> {
    let target = match node {
        Stmt::Assign(assign) if assign.targets.len() == 1 => &assign.targets[0],
        Stmt::AnnAssign(assign) => &assign.target,
        _ => return None,
    };
    match target {
        Expr::Name(name) => Some(name.id.as_str()),
        _ => None,
    }
}
