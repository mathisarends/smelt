//! Local async deadline facts shared by outcome and timing review.
use super::TestFunction;
use crate::checker::analysis::walk::{TreeNode, expression_key};
use ruff_python_ast::{Expr, ExprCall, Stmt};

pub struct AsyncDeadline {
    pub awaited: bool,
    pub bounded: bool,
    pub raises_on_timeout: bool,
}

fn context_timeout(wrapper: &str) -> Option<&'static str> {
    match wrapper {
        "asyncio.timeout" | "anyio.fail_after" | "anyio.move_on_after" => Some("delay"),
        "asyncio.timeout_at" => Some("when"),
        "trio.fail_after" | "trio.move_on_after" => Some("seconds"),
        _ => None,
    }
}

/// Whether `child` is one of the statements of `body`.
pub fn in_body(body: &[Stmt], child: TreeNode<'_>) -> bool {
    body.iter()
        .any(|statement| TreeNode::Statement(statement).key() == child.key())
}

pub fn async_deadline<'a>(
    node: &'a Expr,
    function: &TestFunction<'a>,
    helpers: &[String],
) -> AsyncDeadline {
    let mut awaited = node.is_await_expr();
    let (mut bounded, mut raises, mut may_move_on) = (false, false, false);
    let mut awaitable = node.is_call_expr().then(|| expression_key(node));
    let mut child = TreeNode::Expression(node);
    let mut parent = function.parent(child);
    while let Some(current) = parent {
        if matches!(current, TreeNode::Expression(Expr::Await(_))) && awaitable == Some(child.key())
        {
            awaited = true;
            awaitable = None;
        }
        if let TreeNode::Expression(outer @ Expr::Call(call)) = current {
            let wrapper = function.call_name(outer);
            let wraps = awaitable.is_some_and(|wanted| {
                call.arguments
                    .args
                    .first()
                    .is_some_and(|first| expression_key(first) == wanted)
                    || call.arguments.keywords.iter().any(|keyword| {
                        keyword
                            .arg
                            .as_ref()
                            .is_some_and(|arg| arg.as_str() == "fut")
                            && expression_key(&keyword.value) == wanted
                    })
            });
            if wraps && wrapper == Some("asyncio.wait_for") {
                awaitable = Some(current.key());
                if has_timeout(call, "timeout", Some(1)) {
                    bounded = true;
                    raises = true;
                }
            } else if wraps && is_helper(wrapper, helpers) {
                awaitable = Some(current.key());
                bounded = true;
            }
        }
        if let TreeNode::Statement(Stmt::With(with)) = current
            && in_body(&with.body, child)
        {
            for item in &with.items {
                let Expr::Call(call) = &item.context_expr else {
                    continue;
                };
                let wrapper = function.call_name(&item.context_expr);
                if let Some(keyword) = context_timeout(wrapper.unwrap_or(""))
                    && has_timeout(call, keyword, Some(0))
                {
                    bounded = true;
                    if wrapper.is_some_and(|name| name.ends_with(".move_on_after")) {
                        may_move_on = true;
                    } else {
                        raises = true;
                    }
                }
                bounded |= is_helper(wrapper, helpers);
            }
        }
        child = current;
        parent = function.parent(current);
    }
    // A move_on_after scope may leave successfully before the event is observed.
    AsyncDeadline {
        awaited,
        bounded,
        raises_on_timeout: raises && !may_move_on,
    }
}

fn is_helper(wrapper: Option<&str>, helpers: &[String]) -> bool {
    wrapper.is_some_and(|name| helpers.iter().any(|helper| helper == name))
}

pub fn has_timeout(call: &ExprCall, name: &str, position: Option<usize>) -> bool {
    let value = call
        .arguments
        .keywords
        .iter()
        .find(|keyword| keyword.arg.as_ref().is_some_and(|arg| arg.as_str() == name))
        .map(|keyword| &keyword.value)
        .or_else(|| position.and_then(|index| call.arguments.args.get(index)));
    value.is_some_and(|value| !value.is_none_literal_expr())
}
