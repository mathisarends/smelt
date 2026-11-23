//! MOD010: `modeling/implicit-duration-unit`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{Names, import_bindings, qualified_name};
use crate::checker::analysis::nodes::{is_upper, parse_eval, walk_statements};
use crate::checker::analysis::unparse::unparse;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    Expr, ExprBinOp, ExprCall, Int, Number, Operator, Parameter, ParameterWithDefault, Stmt,
    StmtFunctionDef, UnaryOp,
};
use ruff_text_size::{Ranged, TextSize};
use serde_json::json;

const DURATION_WORDS: [&str; 5] = ["timeout", "delay", "interval", "duration", "wait"];
const EXEMPT_DECORATORS: [&str; 2] = ["overload", "override"];
const WRAPPERS: [&str; 4] = ["Final", "ClassVar", "Optional", "Annotated"];
const NUMBERS: [&str; 2] = ["int", "float"];
const SLEEPS: [&str; 4] = ["asyncio.sleep", "anyio.sleep", "trio.sleep", "time.sleep"];
const CLOCKS: [&str; 3] = ["time.monotonic", "time.time", "time.perf_counter"];

/// The keyword and position of the relative duration argument of a call.
fn duration_argument(qualified: &str) -> Option<(&'static str, Option<usize>)> {
    Some(match qualified {
        "asyncio.timeout" | "asyncio.sleep" => ("delay", Some(0)),
        "asyncio.wait_for" => ("timeout", Some(1)),
        "asyncio.wait" => ("timeout", None),
        "time.sleep" => ("secs", Some(0)),
        "anyio.sleep" | "anyio.fail_after" | "anyio.move_on_after" => ("delay", Some(0)),
        "trio.sleep" | "trio.fail_after" | "trio.move_on_after" => ("seconds", Some(0)),
        _ => return None,
    })
}

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let source = input.parsed.text.text.as_str();
    let bindings = import_bindings(module, &input.source.module, input.source.is_package);
    let mut found = Vec::new();
    assignments(input, &module.body, "constant", &mut found);
    for statement in walk_statements(module) {
        match statement {
            Stmt::ClassDef(node) => assignments(input, &node.body, "field", &mut found),
            Stmt::FunctionDef(node) if !is_exempt(node) => parameters(input, node, &mut found),
            _ => {}
        }
    }
    let mut literals = Literals {
        source,
        bindings: &bindings,
        found: Vec::new(),
    };
    literals.visit_body(&module.body);
    for (value, target) in literals.found {
        found.push(literal_finding(input, value, &target));
    }
    found
}

fn name_finding(input: &RuleInput<'_>, kind: &str, name: &str, offset: TextSize) -> Violation {
    let suffix = if is_upper(name) {
        "_SECONDS"
    } else {
        "_seconds"
    };
    let label = match kind {
        "constant" => "Constant",
        "field" => "Field",
        _ => "Parameter",
    };
    let (line, column) = input.place(offset);
    input.violation(Finding {
        message: format!("{label} {name} holds a duration without a unit"),
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({
            "kind": kind,
            "name": name,
            "suggested_name": format!("{name}{suffix}"),
            "automatic_rewrite": false,
        }),
        hint: format!(
            "Name it {name}{suffix} or after its actual unit, or type it as datetime.timedelta."
        ),
        fix: None,
    })
}

fn literal_finding(input: &RuleInput<'_>, literal: &Expr, target: &str) -> Violation {
    let value = unparse(input.parsed.text.text.as_str(), literal);
    let (line, column) = input.place(literal.start());
    input.violation(Finding {
        message: format!("Inline duration {value} in {target}"),
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({
            "kind": "literal",
            "value": value,
            "target": target,
            "automatic_rewrite": false,
        }),
        hint: format!(
            "Name {value} as a constant or setting that states its purpose and unit, and reuse it at matching call sites."
        ),
        fix: None,
    })
}

/// Calls with an inline duration and literals added to a clock reading.
struct Literals<'s, 'a, 'b> {
    source: &'s str,
    bindings: &'b Names<'a>,
    found: Vec<(&'a Expr, String)>,
}

impl<'a> Literals<'_, 'a, '_> {
    fn call(&mut self, call: &'a ExprCall) {
        let Some(qualified) = qualified_name(&call.func, self.bindings) else {
            return;
        };
        let Some((keyword, position)) = duration_argument(&qualified) else {
            return;
        };
        let by_keyword = call
            .arguments
            .keywords
            .iter()
            .find(|item| item.arg.as_ref().is_some_and(|arg| arg.as_str() == keyword))
            .map(|item| &item.value);
        let value = by_keyword.or_else(|| position.and_then(|at| call.arguments.args.get(at)));
        let Some(value) = value.filter(|value| is_numeric_literal(value)) else {
            return;
        };
        if SLEEPS.contains(&qualified.as_str()) && is_zero(value) {
            return;
        }
        self.found.push((value, format!("{qualified}()")));
    }

    fn addition(&mut self, addition: &'a ExprBinOp) {
        for (clock, value) in [
            (addition.left.as_ref(), addition.right.as_ref()),
            (addition.right.as_ref(), addition.left.as_ref()),
        ] {
            if is_numeric_literal(value) && is_clock(clock, self.bindings) {
                let target = format!("{} + ...", unparse(self.source, clock));
                self.found.push((value, target));
            }
        }
    }
}

impl<'a> Visitor<'a> for Literals<'_, 'a, '_> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Call(node) => self.call(node),
            Expr::BinOp(node) if node.op == Operator::Add => self.addition(node),
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
}

fn is_clock(node: &Expr, bindings: &Names<'_>) -> bool {
    let Expr::Call(call) = node else {
        return false;
    };
    if !call.arguments.args.is_empty() || !call.arguments.keywords.is_empty() {
        return false;
    }
    if qualified_name(&call.func, bindings).is_some_and(|name| CLOCKS.contains(&name.as_str())) {
        return true;
    }
    // Event loop clocks: loop.time(), asyncio.get_running_loop().time().
    matches!(call.func.as_ref(), Expr::Attribute(function) if function.attr.as_str() == "time")
}

fn is_zero(node: &Expr) -> bool {
    let Expr::NumberLiteral(number) = node else {
        return false;
    };
    match &number.value {
        Number::Int(value) => *value == Int::ZERO,
        Number::Float(value) => *value == 0.0,
        Number::Complex { .. } => false,
    }
}

fn assignments(input: &RuleInput<'_>, body: &[Stmt], kind: &str, found: &mut Vec<Violation>) {
    for statement in body {
        match statement {
            Stmt::AnnAssign(node) => {
                let Expr::Name(target) = node.target.as_ref() else {
                    continue;
                };
                let numeric = is_numeric_annotation(&node.annotation)
                    || node.value.as_deref().is_some_and(is_numeric_literal);
                if numeric && is_unitless(target.id.as_str()) {
                    found.push(name_finding(input, kind, target.id.as_str(), node.start()));
                }
            }
            Stmt::Assign(node) if is_numeric_literal(&node.value) => {
                for target in &node.targets {
                    if let Expr::Name(target) = target
                        && is_unitless(target.id.as_str())
                    {
                        found.push(name_finding(input, kind, target.id.as_str(), node.start()));
                    }
                }
            }
            _ => {}
        }
    }
}

fn parameters(input: &RuleInput<'_>, function: &StmtFunctionDef, found: &mut Vec<Violation>) {
    let parameters = &function.parameters;
    let all = parameters
        .posonlyargs
        .iter()
        .chain(&parameters.args)
        .chain(&parameters.kwonlyargs);
    for item in all {
        let ParameterWithDefault {
            parameter, default, ..
        } = item;
        let Parameter {
            name, annotation, ..
        } = parameter;
        let numeric = match annotation.as_deref() {
            Some(annotation) => is_numeric_annotation(annotation),
            None => default.as_deref().is_some_and(is_numeric_literal),
        };
        if numeric && is_unitless(name.as_str()) {
            found.push(name_finding(
                input,
                "parameter",
                name.as_str(),
                parameter.start(),
            ));
        }
    }
}

fn is_exempt(function: &StmtFunctionDef) -> bool {
    function.decorator_list.iter().any(|decorator| {
        let target = match &decorator.expression {
            Expr::Call(call) => call.func.as_ref(),
            other => other,
        };
        last_name(target).is_some_and(|name| EXEMPT_DECORATORS.contains(&name))
    })
}

fn is_unitless(name: &str) -> bool {
    let lowered = name.to_lowercase();
    lowered
        .split('_')
        .rfind(|word| !word.is_empty())
        .is_some_and(|word| DURATION_WORDS.contains(&word))
}

fn last_name(node: &Expr) -> Option<&str> {
    match node {
        Expr::Name(item) => Some(item.id.as_str()),
        Expr::Attribute(item) => Some(item.attr.as_str()),
        _ => None,
    }
}

fn is_numeric_annotation(annotation: &Expr) -> bool {
    if let Expr::StringLiteral(text) = annotation {
        return parse_eval(text.value.to_str()).is_some_and(|parsed| names_number(parsed.expr()));
    }
    names_number(annotation)
}

fn names_number(annotation: &Expr) -> bool {
    match annotation {
        Expr::Subscript(node)
            if last_name(&node.value).is_some_and(|name| WRAPPERS.contains(&name)) =>
        {
            match node.slice.as_ref() {
                Expr::Tuple(items) if !items.elts.is_empty() => {
                    is_numeric_annotation(&items.elts[0])
                }
                inner => is_numeric_annotation(inner),
            }
        }
        Expr::BinOp(node) if node.op == Operator::BitOr => {
            let mut kept = [node.left.as_ref(), node.right.as_ref()]
                .into_iter()
                .filter(|side| !side.is_none_literal_expr());
            match (kept.next(), kept.next()) {
                (Some(side), None) => is_numeric_annotation(side),
                _ => false,
            }
        }
        other => last_name(other).is_some_and(|name| NUMBERS.contains(&name)),
    }
}

fn is_numeric_literal(value: &Expr) -> bool {
    let value = match value {
        Expr::UnaryOp(node) if node.op == UnaryOp::USub => node.operand.as_ref(),
        other => other,
    };
    matches!(
        value,
        Expr::NumberLiteral(number) if !matches!(number.value, Number::Complex { .. })
    )
}
