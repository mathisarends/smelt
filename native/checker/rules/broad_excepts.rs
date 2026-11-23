//! IDM006: `idioms/missing-broad-except-reason`.
use super::{Finding, RuleInput};
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use regex::Regex;
use ruff_python_ast::token::TokenKind;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{ExceptHandler, ExceptHandlerExceptHandler, Expr, Stmt};
use ruff_text_size::{Ranged, TextSize};
use serde_json::json;
use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

const BROAD: [&str; 2] = ["Exception", "BaseException"];
const BARE: &str = "bare";
const SEPARATORS: [char; 7] = [' ', '\t', '-', ':', ';', ',', '.'];
const HINT: &str = "Catch the specific exceptions you can handle, or add a comment explaining why this handler must be broad.";

/// Tool directives. The classes spell out what Python's `re` matches and Rust's does not:
/// `\s` includes U+001C to U+001F, `\w` is `str.isalnum` plus `_`, and `re.IGNORECASE` folds
/// `i` with U+0130 and U+0131.
static DIRECTIVE: LazyLock<Regex> = LazyLock::new(|| {
    let pattern = r"(?ix)\A S*(?:
        noqa(?: : S* C+[0-9]+ (?: [\s\x1c-\x1f,]+ C+[0-9]+ )* )?
      | pylInt: S* dIsable (?: -next )? = W+ (?: S* , S* W+ )*
      | (?: type | pyrIght ): S* Ignore (?: \[[^\]]*\] )?
      | pragma: S* no S+ (?: cover | branch )
      | smelt: S* Ignore (?: -fIle )? (?: \[[^\]]*\] )?
      | fmt: S* (?: on | off | skIp )
    )";
    let expanded = pattern
        .replace("S*", r"[\s\x1c-\x1f]*")
        .replace("S+", r"[\s\x1c-\x1f]+")
        .replace("C+", r"[A-Z\x{130}\x{131}]+")
        .replace("W+", r"[\p{L}\p{N}_-]+")
        .replace('I', r"[i\x{130}\x{131}]");
    Regex::new(&expanded).expect("the directive pattern is valid")
});
/// `str.isalnum`.
static ALPHANUMERIC: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[\p{L}\p{N}]").expect("the pattern is valid"));

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let mut collector = Handlers { found: Vec::new() };
    collector.visit_body(&input.parsed.module().body);
    let mut handlers: Vec<(&ExceptHandlerExceptHandler, &str)> = collector
        .found
        .into_iter()
        .filter_map(|handler| {
            let caught = broad_type(handler)?;
            (!always_raises(handler)).then_some((handler, caught))
        })
        .collect();
    if handlers.is_empty() {
        return Vec::new();
    }
    handlers.sort_by_key(|(handler, _)| handler.start());
    let comments = Comments::scan(input.parsed);
    let mut found = Vec::new();
    for (handler, caught) in handlers {
        let (line, column) = input.place(handler.start());
        let (body_line, _) = input.place(statement_start(input.parsed, &handler.body[0]));
        if comments.explains(line, body_line) {
            continue;
        }
        let subject = if caught == BARE {
            "Bare except".to_owned()
        } else {
            format!("Handler for {caught}")
        };
        found.push(input.violation(Finding {
            message: format!("{subject} does not say why it catches every failure"),
            line,
            column,
            end_line: None,
            end_column: None,
            expected: json!({"caught": caught, "automatic_rewrite": false}),
            hint: HINT.to_owned(),
            fix: None,
        }));
    }
    found
}

fn statement_start(parsed: &ParsedSource, statement: &Stmt) -> TextSize {
    match statement {
        Stmt::FunctionDef(node) => parsed.function_start(node),
        Stmt::ClassDef(node) => parsed.class_start(node),
        other => other.start(),
    }
}

struct Comments<'a> {
    texts: HashMap<usize, &'a str>,
    standalone: HashSet<usize>,
}

impl<'a> Comments<'a> {
    fn scan(parsed: &'a ParsedSource) -> Self {
        let text = parsed.text.text.as_str();
        let mut texts = HashMap::new();
        let mut standalone = HashSet::new();
        for token in parsed.parsed.tokens() {
            if token.kind() != TokenKind::Comment {
                continue;
            }
            let start = usize::from(token.start());
            let (line, column) = parsed.text.position(start);
            texts.insert(line, &text[start..usize::from(token.end())]);
            if text[start - column..start].trim().is_empty() {
                standalone.insert(line);
            }
        }
        Self { texts, standalone }
    }

    /// The except header, lines up to the first body statement, and comments right above.
    fn explains(&self, handler_line: usize, body_line: usize) -> bool {
        let mut lines: Vec<usize> = (handler_line..=body_line).collect();
        let mut above = handler_line - 1;
        while self.standalone.contains(&above) {
            lines.push(above);
            above -= 1;
        }
        lines
            .iter()
            .filter_map(|line| self.texts.get(line))
            .any(|text| is_reason(text))
    }
}

fn is_reason(comment: &str) -> bool {
    comment.split('#').any(|segment| {
        let mut rest = segment;
        while let Some(directive) = DIRECTIVE.find(rest) {
            rest = rest[directive.end()..].trim_start_matches(SEPARATORS);
        }
        ALPHANUMERIC.is_match(rest)
    })
}

fn broad_type(handler: &ExceptHandlerExceptHandler) -> Option<&str> {
    let Some(caught) = handler.type_.as_deref() else {
        return Some(BARE);
    };
    let candidates = match caught {
        Expr::Tuple(node) => node.elts.as_slice(),
        other => std::slice::from_ref(other),
    };
    candidates.iter().find_map(|candidate| {
        let name = match candidate {
            Expr::Name(node) => node.id.as_str(),
            Expr::Attribute(node)
                if matches!(node.value.as_ref(), Expr::Name(owner) if owner.id.as_str() == "builtins") =>
            {
                node.attr.as_str()
            }
            _ => return None,
        };
        BROAD.contains(&name).then_some(name)
    })
}

fn always_raises(handler: &ExceptHandlerExceptHandler) -> bool {
    if !matches!(handler.body.last(), Some(Stmt::Raise(_))) {
        return false;
    }
    let mut exits = LocalExits { found: false };
    exits.visit_body(&handler.body);
    !exits.found
}

/// A return, break or continue outside nested functions and classes.
struct LocalExits {
    found: bool,
}

impl<'a> Visitor<'a> for LocalExits {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => {}
            Stmt::Return(_) | Stmt::Break(_) | Stmt::Continue(_) => self.found = true,
            other => visitor::walk_stmt(self, other),
        }
    }

    fn visit_expr(&mut self, _expression: &'a Expr) {}
}

struct Handlers<'a> {
    found: Vec<&'a ExceptHandlerExceptHandler>,
}

impl<'a> Visitor<'a> for Handlers<'a> {
    fn visit_except_handler(&mut self, handler: &'a ExceptHandler) {
        let ExceptHandler::ExceptHandler(node) = handler;
        self.found.push(node);
        visitor::walk_except_handler(self, handler);
    }

    fn visit_expr(&mut self, _expression: &'a Expr) {}
}
