//! Conservative evidence that all recognized checks can vanish on empty output.
use super::deadlines::async_deadline;
use super::helpers::outcome_propagates;
use super::subprocesses::child_status_propagates;
use super::{HelperCall, TestFunction, TestModule, nonempty_literal};
use crate::checker::analysis::walk::{NodeKey, TreeNode, expression_key};
use ruff_python_ast::{Comprehension, Expr, Stmt};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::HashSet;

/// One recognized outcome check.
#[derive(Clone, Copy)]
pub enum Evidence<'a> {
    Assert(&'a Stmt),
    Call(&'a Expr),
}

impl<'a> Evidence<'a> {
    fn node(self) -> TreeNode<'a> {
        match self {
            Self::Assert(statement) => TreeNode::Statement(statement),
            Self::Call(call) => TreeNode::Expression(call),
        }
    }
}

/// Why every outcome of a test can pass on empty data.
#[derive(Clone, Copy)]
pub enum Risk<'a> {
    Loop(&'a Stmt),
    All(&'a Expr),
}

impl Risk<'_> {
    fn key(self) -> NodeKey {
        match self {
            Self::Loop(statement) => TreeNode::Statement(statement).key(),
            Self::All(call) => expression_key(call),
        }
    }

    pub fn start(self) -> ruff_text_size::TextSize {
        match self {
            Self::Loop(statement) => statement.start(),
            Self::All(call) => call.start(),
        }
    }

    pub const fn kind(self) -> &'static str {
        match self {
            Self::Loop(_) => "loop",
            Self::All(_) => "all",
        }
    }
}

impl<'a> TestFunction<'a> {
    pub fn has_assertion(&self, module: &TestModule<'a>, configured: &[String]) -> bool {
        !self.outcome_evidence(module, configured).is_empty()
    }

    pub fn outcome_evidence(
        &self,
        module: &TestModule<'a>,
        configured: &[String],
    ) -> Vec<Evidence<'a>> {
        let mut evidence: Vec<Evidence<'a>> = self
            .assertions
            .iter()
            .filter(|statement| {
                !matches!(statement, Stmt::Assert(item)
                    if matches!(item.test.as_ref(), Expr::BooleanLiteral(value) if value.value))
            })
            .map(|statement| Evidence::Assert(statement))
            .collect();
        for call in &self.calls {
            let name = call.qualified.as_str();
            let raises = matches!(
                name,
                "pytest.raises" | "pytest.warns" | "pytest.deprecated_call"
            ) && {
                let required = usize::from(name != "pytest.deprecated_call");
                matches!(
                    self.parent(TreeNode::Expression(call.node)),
                    Some(TreeNode::Item(_))
                ) || matches!(call.node, Expr::Call(item) if item.arguments.args.len() > required)
            };
            if configured.iter().any(|helper| helper == name)
                || raises
                || name.starts_with("unittest.TestCase.assert")
                || call.interaction_assertion()
            {
                evidence.push(Evidence::Call(call.node));
            }
        }
        for call in &self.calls {
            if matches!(
                call.qualified.as_str(),
                "asyncio.Event.wait" | "asyncio.locks.Event.wait"
            ) {
                let deadline = async_deadline(call.node, self, &[]);
                if deadline.awaited && deadline.raises_on_timeout {
                    evidence.push(Evidence::Call(call.node));
                }
            }
        }
        evidence.extend(
            self.child_assertions
                .iter()
                .filter(|call| child_status_propagates(call, self))
                .map(|call| Evidence::Call(call)),
        );
        for helper in &self.helpers {
            if !helper.outcomes(module, configured).is_empty()
                && outcome_propagates(TreeNode::Expression(helper.node), self)
            {
                evidence.push(Evidence::Call(helper.node));
            }
        }
        evidence
    }
}

impl<'a> HelperCall<'a> {
    fn function<'m>(&self, module: &'m TestModule<'a>) -> &'m TestFunction<'a> {
        &module.helpers[&self.line]
    }

    fn outcomes(&self, module: &TestModule<'a>, configured: &[String]) -> Vec<Evidence<'a>> {
        let function = self.function(module);
        function
            .outcome_evidence(module, configured)
            .into_iter()
            .filter(|item| outcome_propagates(item.node(), function))
            .collect()
    }
}

/// Report only when every recognized outcome is loop or `all` dependent.
pub fn vacuous_outcomes<'a>(
    test: &TestFunction<'a>,
    module: &TestModule<'a>,
    configured: &[String],
) -> Vec<Risk<'a>> {
    vacuous_evidence(
        test,
        module,
        configured,
        &test.outcome_evidence(module, configured),
    )
}

/// Explain existing inference boundaries without following additional bodies.
pub fn helper_outcome_review<'a>(
    test: &TestFunction<'a>,
    module: &TestModule<'a>,
    configured: &[String],
) -> Vec<Value> {
    let mut review = Vec::new();
    for call in &test.calls {
        let key = expression_key(call.node);
        let followed = test
            .helpers
            .iter()
            .rev()
            .find(|helper| expression_key(helper.node) == key);
        let reason = if let Some(helper) = followed {
            let outcomes = helper.outcomes(module, configured);
            if !helper
                .function(module)
                .outcome_evidence(module, configured)
                .is_empty()
                && outcomes.is_empty()
            {
                "helper-context-may-swallow-evidence"
            } else if !outcomes.is_empty()
                && !outcome_propagates(TreeNode::Expression(call.node), test)
            {
                "caller-context-may-swallow-evidence"
            } else {
                "no-recognized-evidence-within-one-hop"
            }
        } else if call.helper_line.is_some() {
            "local-coroutine-not-recognized-as-executed"
        } else if call.provenance == "import" {
            "imported-body-not-analyzed"
        } else {
            continue;
        };
        review.push(json!({
            "call": call.qualified,
            "line": module.line(call.node.start()),
            "reason": reason,
        }));
    }
    review
}

fn vacuous_evidence<'a>(
    test: &TestFunction<'a>,
    module: &TestModule<'a>,
    configured: &[String],
    evidence: &[Evidence<'a>],
) -> Vec<Risk<'a>> {
    if evidence.is_empty() {
        return Vec::new();
    }
    let root = TreeNode::Statement(test.statement).key();
    let universal: HashSet<NodeKey> = test
        .calls
        .iter()
        .filter(|call| call.qualified == "builtins.all")
        .map(|call| expression_key(call.node))
        .collect();
    let mut risks: Vec<Risk<'a>> = Vec::new();
    for outcome in evidence {
        let mut child = outcome.node();
        let mut parent = test.parent(child);
        let mut risky = None;
        while let Some(current) = parent {
            if current.key() == root {
                break;
            }
            if let TreeNode::Statement(statement @ Stmt::For(node)) = current
                && super::deadlines::in_body(&node.body, child)
                && possibly_empty(&node.iter, &test.nonempty_iterables)
            {
                risky = Some(Risk::Loop(statement));
                break;
            }
            child = current;
            parent = test.parent(current);
        }
        if risky.is_none()
            && let Evidence::Assert(Stmt::Assert(item)) = outcome
            && let expression @ Expr::Call(call) = item.test.as_ref()
            && universal.contains(&expression_key(expression))
            && call
                .arguments
                .args
                .first()
                .is_some_and(|first| possibly_empty(first, &test.nonempty_iterables))
        {
            risky = Some(Risk::All(expression));
        }
        if risky.is_none()
            && let Evidence::Call(call) = outcome
            && let Some(execution) = test
                .helpers
                .iter()
                .rev()
                .find(|helper| expression_key(helper.node) == expression_key(call))
        {
            let helper_risks = vacuous_evidence(
                execution.function(module),
                module,
                configured,
                &execution.outcomes(module, configured),
            );
            risky = helper_risks.first().copied();
        }
        let Some(risky) = risky else {
            return Vec::new();
        };
        if !risks.iter().any(|item| item.key() == risky.key()) {
            risks.push(risky);
        }
    }
    risks
}

fn possibly_empty(node: &Expr, known: &HashSet<NodeKey>) -> bool {
    let generators: &[Comprehension] = match node {
        Expr::Generator(item) => &item.generators,
        Expr::ListComp(item) => &item.generators,
        Expr::SetComp(item) => &item.generators,
        Expr::DictComp(item) => &item.generators,
        other => return !known.contains(&expression_key(other)) && !nonempty_literal(other),
    };
    generators.iter().any(|generator| {
        !generator.ifs.is_empty() || generator.is_async || possibly_empty(&generator.iter, known)
    })
}
