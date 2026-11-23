//! `ast.walk` order over Ruff's tree, for results whose order is observable.
//!
//! CPython's tree has levels Ruff's lacks (an `elif` is a nested `If`, a formatted
//! value is a node) and lists fields in another order. Leaf nodes such as aliases
//! and string constants are left out because they cannot change the order.
use ruff_python_ast::{
    Comprehension, ElifElseClause, ExceptHandler, Expr, FStringPartRef, InterpolatedElement,
    InterpolatedStringElement, InterpolatedStringElements, Keyword, MatchCase, ModModule,
    Parameter, Parameters, Pattern, Stmt, TypeParam, WithItem,
};

#[derive(Clone, Copy)]
pub enum TreeNode<'a> {
    Statement(&'a Stmt),
    Expression(&'a Expr),
    Elif(&'a [ElifElseClause]),
    Handler(&'a ExceptHandler),
    Case(&'a MatchCase),
    Pattern(&'a Pattern),
    Comprehension(&'a Comprehension),
    Keyword(&'a Keyword),
    Item(&'a WithItem),
    Parameters(&'a Parameters),
    Parameter(&'a Parameter),
    TypeParam(&'a TypeParam),
    Interpolation(&'a InterpolatedElement),
    Spec(&'a InterpolatedStringElements),
}

/// Every node of the module in `ast.walk` order.
pub fn tree_nodes(module: &ModModule) -> Vec<TreeNode<'_>> {
    walk(module.body.iter().map(TreeNode::Statement).collect())
}

/// `root` and everything below it in `ast.walk` order.
pub fn nodes_below(root: TreeNode<'_>) -> Vec<TreeNode<'_>> {
    walk(vec![root])
}

fn walk(mut ordered: Vec<TreeNode<'_>>) -> Vec<TreeNode<'_>> {
    let mut index = 0;
    while let Some(node) = ordered.get(index).copied() {
        push_children(node, &mut ordered);
        index += 1;
    }
    ordered
}

fn push_children<'a>(node: TreeNode<'a>, out: &mut Vec<TreeNode<'a>>) {
    match node {
        TreeNode::Statement(statement) => push_statement(statement, out),
        TreeNode::Expression(expression) => push_expression(expression, out),
        TreeNode::Elif(clauses) => {
            if let Some((clause, rest)) = clauses.split_first() {
                push_optional(clause.test.as_ref(), out);
                push_body(&clause.body, out);
                push_clauses(rest, out);
            }
        }
        TreeNode::Handler(handler) => {
            let ExceptHandler::ExceptHandler(handler) = handler;
            push_optional(handler.type_.as_deref(), out);
            push_body(&handler.body, out);
        }
        TreeNode::Case(case) => {
            out.push(TreeNode::Pattern(&case.pattern));
            push_optional(case.guard.as_deref(), out);
            push_body(&case.body, out);
        }
        TreeNode::Pattern(pattern) => push_pattern(pattern, out),
        TreeNode::Comprehension(comprehension) => {
            out.push(TreeNode::Expression(&comprehension.target));
            out.push(TreeNode::Expression(&comprehension.iter));
            push_all(&comprehension.ifs, out);
        }
        TreeNode::Keyword(keyword) => out.push(TreeNode::Expression(&keyword.value)),
        TreeNode::Item(item) => {
            out.push(TreeNode::Expression(&item.context_expr));
            push_optional(item.optional_vars.as_deref(), out);
        }
        TreeNode::Parameters(parameters) => push_parameters(parameters, out),
        TreeNode::Parameter(parameter) => push_optional(parameter.annotation(), out),
        TreeNode::TypeParam(parameter) => match parameter {
            TypeParam::TypeVar(item) => {
                push_optional(item.bound.as_deref(), out);
                push_optional(item.default.as_deref(), out);
            }
            TypeParam::ParamSpec(item) => push_optional(item.default.as_deref(), out),
            TypeParam::TypeVarTuple(item) => push_optional(item.default.as_deref(), out),
        },
        TreeNode::Interpolation(element) => {
            out.push(TreeNode::Expression(&element.expression));
            if let Some(spec) = &element.format_spec {
                out.push(TreeNode::Spec(&spec.elements));
            }
        }
        TreeNode::Spec(elements) => push_interpolations(elements, out),
    }
}

fn push_statement<'a>(statement: &'a Stmt, out: &mut Vec<TreeNode<'a>>) {
    match statement {
        Stmt::FunctionDef(node) => {
            out.push(TreeNode::Parameters(&node.parameters));
            push_body(&node.body, out);
            out.extend(
                node.decorator_list
                    .iter()
                    .map(|decorator| TreeNode::Expression(&decorator.expression)),
            );
            push_optional(node.returns.as_deref(), out);
            push_type_params(node.type_params.as_deref(), out);
        }
        Stmt::ClassDef(node) => {
            push_all(node.bases(), out);
            out.extend(node.keywords().iter().map(TreeNode::Keyword));
            push_body(&node.body, out);
            out.extend(
                node.decorator_list
                    .iter()
                    .map(|decorator| TreeNode::Expression(&decorator.expression)),
            );
            push_type_params(node.type_params.as_deref(), out);
        }
        Stmt::Return(node) => push_optional(node.value.as_deref(), out),
        Stmt::Delete(node) => push_all(&node.targets, out),
        Stmt::TypeAlias(node) => {
            out.push(TreeNode::Expression(&node.name));
            push_type_params(node.type_params.as_deref(), out);
            out.push(TreeNode::Expression(&node.value));
        }
        Stmt::Assign(node) => {
            push_all(&node.targets, out);
            out.push(TreeNode::Expression(&node.value));
        }
        Stmt::AugAssign(node) => {
            out.push(TreeNode::Expression(&node.target));
            out.push(TreeNode::Expression(&node.value));
        }
        Stmt::AnnAssign(node) => {
            out.push(TreeNode::Expression(&node.target));
            out.push(TreeNode::Expression(&node.annotation));
            push_optional(node.value.as_deref(), out);
        }
        Stmt::For(node) => {
            out.push(TreeNode::Expression(&node.target));
            out.push(TreeNode::Expression(&node.iter));
            push_body(&node.body, out);
            push_body(&node.orelse, out);
        }
        Stmt::While(node) => {
            out.push(TreeNode::Expression(&node.test));
            push_body(&node.body, out);
            push_body(&node.orelse, out);
        }
        Stmt::If(node) => {
            out.push(TreeNode::Expression(&node.test));
            push_body(&node.body, out);
            push_clauses(&node.elif_else_clauses, out);
        }
        Stmt::With(node) => {
            out.extend(node.items.iter().map(TreeNode::Item));
            push_body(&node.body, out);
        }
        Stmt::Match(node) => {
            out.push(TreeNode::Expression(&node.subject));
            out.extend(node.cases.iter().map(TreeNode::Case));
        }
        Stmt::Raise(node) => {
            push_optional(node.exc.as_deref(), out);
            push_optional(node.cause.as_deref(), out);
        }
        Stmt::Try(node) => {
            push_body(&node.body, out);
            out.extend(node.handlers.iter().map(TreeNode::Handler));
            push_body(&node.orelse, out);
            push_body(&node.finalbody, out);
        }
        Stmt::Assert(node) => {
            out.push(TreeNode::Expression(&node.test));
            push_optional(node.msg.as_deref(), out);
        }
        Stmt::Expr(node) => out.push(TreeNode::Expression(&node.value)),
        _ => {}
    }
}

fn push_expression<'a>(expression: &'a Expr, out: &mut Vec<TreeNode<'a>>) {
    match expression {
        Expr::BoolOp(node) => push_all(&node.values, out),
        Expr::Named(node) => {
            out.push(TreeNode::Expression(&node.target));
            out.push(TreeNode::Expression(&node.value));
        }
        Expr::BinOp(node) => {
            out.push(TreeNode::Expression(&node.left));
            out.push(TreeNode::Expression(&node.right));
        }
        Expr::UnaryOp(node) => out.push(TreeNode::Expression(&node.operand)),
        Expr::Lambda(node) => {
            if let Some(parameters) = node.parameters.as_deref() {
                out.push(TreeNode::Parameters(parameters));
            }
            out.push(TreeNode::Expression(&node.body));
        }
        Expr::If(node) => {
            out.push(TreeNode::Expression(&node.test));
            out.push(TreeNode::Expression(&node.body));
            out.push(TreeNode::Expression(&node.orelse));
        }
        // CPython holds separate lists: every key comes before every value.
        Expr::Dict(node) => {
            out.extend(
                node.items
                    .iter()
                    .filter_map(|item| item.key.as_ref())
                    .map(TreeNode::Expression),
            );
            out.extend(
                node.items
                    .iter()
                    .map(|item| TreeNode::Expression(&item.value)),
            );
        }
        Expr::Set(node) => push_all(&node.elts, out),
        Expr::ListComp(node) => {
            out.push(TreeNode::Expression(&node.elt));
            out.extend(node.generators.iter().map(TreeNode::Comprehension));
        }
        Expr::SetComp(node) => {
            out.push(TreeNode::Expression(&node.elt));
            out.extend(node.generators.iter().map(TreeNode::Comprehension));
        }
        Expr::DictComp(node) => {
            push_optional(node.key.as_deref(), out);
            out.push(TreeNode::Expression(&node.value));
            out.extend(node.generators.iter().map(TreeNode::Comprehension));
        }
        Expr::Generator(node) => {
            out.push(TreeNode::Expression(&node.elt));
            out.extend(node.generators.iter().map(TreeNode::Comprehension));
        }
        Expr::Await(node) => out.push(TreeNode::Expression(&node.value)),
        Expr::Yield(node) => push_optional(node.value.as_deref(), out),
        Expr::YieldFrom(node) => out.push(TreeNode::Expression(&node.value)),
        Expr::Compare(node) => push_all(&node.operands, out),
        Expr::Call(node) => {
            out.push(TreeNode::Expression(&node.func));
            push_all(&node.arguments.args, out);
            out.extend(node.arguments.keywords.iter().map(TreeNode::Keyword));
        }
        Expr::FString(node) => {
            for part in &node.value {
                if let FStringPartRef::FString(fstring) = part {
                    push_interpolations(&fstring.elements, out);
                }
            }
        }
        Expr::TString(node) => {
            for template in &node.value {
                push_interpolations(&template.elements, out);
            }
        }
        Expr::Attribute(node) => out.push(TreeNode::Expression(&node.value)),
        Expr::Subscript(node) => {
            out.push(TreeNode::Expression(&node.value));
            out.push(TreeNode::Expression(&node.slice));
        }
        Expr::Starred(node) => out.push(TreeNode::Expression(&node.value)),
        Expr::List(node) => push_all(&node.elts, out),
        Expr::Tuple(node) => push_all(&node.elts, out),
        Expr::Slice(node) => {
            push_optional(node.lower.as_deref(), out);
            push_optional(node.upper.as_deref(), out);
            push_optional(node.step.as_deref(), out);
        }
        _ => {}
    }
}

fn push_pattern<'a>(pattern: &'a Pattern, out: &mut Vec<TreeNode<'a>>) {
    match pattern {
        Pattern::MatchValue(node) => out.push(TreeNode::Expression(&node.value)),
        Pattern::MatchSequence(node) => out.extend(node.patterns.iter().map(TreeNode::Pattern)),
        Pattern::MatchMapping(node) => {
            push_all(&node.keys, out);
            out.extend(node.patterns.iter().map(TreeNode::Pattern));
        }
        Pattern::MatchClass(node) => {
            out.push(TreeNode::Expression(&node.cls));
            out.extend(node.arguments.patterns.iter().map(TreeNode::Pattern));
            out.extend(
                node.arguments
                    .keywords
                    .iter()
                    .map(|keyword| TreeNode::Pattern(&keyword.pattern)),
            );
        }
        Pattern::MatchAs(node) => {
            if let Some(inner) = node.pattern.as_deref() {
                out.push(TreeNode::Pattern(inner));
            }
        }
        Pattern::MatchOr(node) => out.extend(node.patterns.iter().map(TreeNode::Pattern)),
        Pattern::MatchSingleton(_) | Pattern::MatchStar(_) => {}
    }
}

/// `posonlyargs, args, vararg, kwonlyargs, kw_defaults, kwarg, defaults`.
fn push_parameters<'a>(parameters: &'a Parameters, out: &mut Vec<TreeNode<'a>>) {
    let positional = || parameters.posonlyargs.iter().chain(&parameters.args);
    out.extend(positional().map(|item| TreeNode::Parameter(&item.parameter)));
    if let Some(vararg) = parameters.vararg.as_deref() {
        out.push(TreeNode::Parameter(vararg));
    }
    out.extend(
        parameters
            .kwonlyargs
            .iter()
            .map(|item| TreeNode::Parameter(&item.parameter)),
    );
    out.extend(
        parameters
            .kwonlyargs
            .iter()
            .filter_map(|item| item.default.as_deref())
            .map(TreeNode::Expression),
    );
    if let Some(kwarg) = parameters.kwarg.as_deref() {
        out.push(TreeNode::Parameter(kwarg));
    }
    out.extend(
        positional()
            .filter_map(|item| item.default.as_deref())
            .map(TreeNode::Expression),
    );
}

fn push_clauses<'a>(clauses: &'a [ElifElseClause], out: &mut Vec<TreeNode<'a>>) {
    match clauses.first() {
        Some(clause) if clause.test.is_some() => out.push(TreeNode::Elif(clauses)),
        Some(clause) => push_body(&clause.body, out),
        None => {}
    }
}

fn push_interpolations<'a>(elements: &'a InterpolatedStringElements, out: &mut Vec<TreeNode<'a>>) {
    for element in elements {
        if let InterpolatedStringElement::Interpolation(value) = element {
            out.push(TreeNode::Interpolation(value));
        }
    }
}

fn push_type_params<'a>(
    parameters: Option<&'a ruff_python_ast::TypeParams>,
    out: &mut Vec<TreeNode<'a>>,
) {
    if let Some(parameters) = parameters {
        out.extend(parameters.type_params.iter().map(TreeNode::TypeParam));
    }
}

fn push_body<'a>(body: &'a [Stmt], out: &mut Vec<TreeNode<'a>>) {
    out.extend(body.iter().map(TreeNode::Statement));
}

fn push_all<'a>(items: &'a [Expr], out: &mut Vec<TreeNode<'a>>) {
    out.extend(items.iter().map(TreeNode::Expression));
}

fn push_optional<'a>(item: Option<&'a Expr>, out: &mut Vec<TreeNode<'a>>) {
    out.extend(item.map(TreeNode::Expression));
}
