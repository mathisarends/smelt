//! Known blocking and wait families and local syntactic deadline guards.
use super::deadlines::{async_deadline, has_timeout};
use super::{TestFunction, TestModule};
use crate::checker::analysis::walk::{NodeKey, expression_key};
use ruff_python_ast::Expr;
use ruff_text_size::Ranged;
use std::collections::{BTreeSet, HashMap};

const PROCESS_WAITS: [&str; 6] = [
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen.wait",
    "subprocess.Popen.communicate",
];
const ASYNC_WAITS: [&str; 4] = [
    "asyncio.Event.wait",
    "asyncio.locks.Event.wait",
    "asyncio.subprocess.Process.wait",
    "asyncio.subprocess.Process.communicate",
];

#[derive(Clone)]
pub struct TestWait<'a> {
    pub node: &'a Expr,
    pub qualified: String,
    pub provenance: &'static str,
    pub helper: Option<String>,
    pub helper_call_line: Option<usize>,
    pub helper_line: Option<usize>,
    pub helper_call_lines: Vec<usize>,
}

impl<'a> TestWait<'a> {
    fn new(node: &'a Expr, qualified: &str, provenance: &'static str) -> Self {
        Self {
            node,
            qualified: qualified.to_owned(),
            provenance,
            helper: None,
            helper_call_line: None,
            helper_line: None,
            helper_call_lines: Vec::new(),
        }
    }
}

pub fn unbounded_waits<'a>(
    test: &TestFunction<'a>,
    module: &TestModule<'a>,
    helpers: &[String],
) -> Vec<TestWait<'a>> {
    let mut waits: Vec<TestWait<'a>> = test
        .calls
        .iter()
        .filter(|call| {
            let name = call.qualified.as_str();
            PROCESS_WAITS.contains(&name) || ASYNC_WAITS.contains(&name)
        })
        .map(|call| TestWait::new(call.node, &call.qualified, call.provenance))
        .collect();
    waits.extend(
        test.awaited_tasks
            .iter()
            .map(|task| TestWait::new(task.node, &task.qualified, task.provenance)),
    );
    let mut unbounded: Vec<TestWait<'a>> = Vec::new();
    for wait in waits {
        if PROCESS_WAITS.contains(&wait.qualified.as_str()) {
            // Async cancellation cannot interrupt a blocking synchronous process call.
            let position = match wait.qualified.as_str() {
                "subprocess.Popen.wait" => Some(0),
                "subprocess.Popen.communicate" => Some(1),
                _ => None,
            };
            if let Expr::Call(call) = wait.node
                && !has_timeout(call, "timeout", position)
            {
                unbounded.push(wait);
            }
            continue;
        }
        let deadline = async_deadline(wait.node, test, helpers);
        if deadline.awaited && !deadline.bounded {
            unbounded.push(wait);
        }
    }
    let mut positions: HashMap<NodeKey, usize> = unbounded
        .iter()
        .enumerate()
        .map(|(index, wait)| (expression_key(wait.node), index))
        .collect();
    for helper in &test.helpers {
        let function = &module.helpers[&helper.line];
        let outer = async_deadline(helper.node, test, helpers);
        let call_line = module.line(helper.node.start());
        for wait in unbounded_waits(function, module, helpers) {
            // A caller's async deadline can guard an awaited coroutine's waits,
            // but cannot interrupt a synchronous subprocess inside that helper.
            if !PROCESS_WAITS.contains(&wait.qualified.as_str()) && outer.bounded {
                continue;
            }
            let key = expression_key(wait.node);
            if let Some(index) = positions.get(&key) {
                let previous = &mut unbounded[*index];
                if previous.helper.is_some() {
                    let lines: BTreeSet<usize> = previous
                        .helper_call_lines
                        .iter()
                        .copied()
                        .chain([call_line])
                        .collect();
                    previous.helper_call_lines = lines.into_iter().collect();
                }
                continue;
            }
            positions.insert(key, unbounded.len());
            unbounded.push(TestWait {
                node: wait.node,
                qualified: wait.qualified,
                provenance: wait.provenance,
                helper: Some(function.name.clone()),
                helper_call_line: Some(call_line),
                helper_line: Some(module.function_line(function.node)),
                helper_call_lines: vec![call_line],
            });
        }
    }
    unbounded
}
