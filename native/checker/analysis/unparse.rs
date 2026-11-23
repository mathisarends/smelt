//! `ast.unparse` for expressions, because its spelling appears in findings.
use super::repr::{
    bytes_repr, float_repr, imaginary_repr, int_repr, is_printable, str_repr, unicode_escape,
};
use ruff_python_ast::{
    BoolOp, CmpOp, Comprehension, ConversionFlag, Expr, InterpolatedElement,
    InterpolatedStringElement, InterpolatedStringElements, Keyword, Number, Operator, Parameter,
    Parameters, UnaryOp, str_prefix::StringLiteralPrefix,
};
use ruff_python_ast::{FStringPartRef, FStringValue};
use ruff_text_size::Ranged;

const NAMED_EXPR: u8 = 1;
const TUPLE: u8 = 2;
const YIELD: u8 = 3;
const TEST: u8 = 4;
const OR: u8 = 5;
const AND: u8 = 6;
const NOT: u8 = 7;
const CMP: u8 = 8;
const EXPR: u8 = 9;
const BXOR: u8 = 10;
const BAND: u8 = 11;
const SHIFT: u8 = 12;
const ARITH: u8 = 13;
const TERM: u8 = 14;
const FACTOR: u8 = 15;
const POWER: u8 = 16;
const AWAIT: u8 = 17;
const ATOM: u8 = 18;

const INFINITY: &str = "1e309";
const ALL_QUOTES: [&str; 4] = ["'", "\"", "\"\"\"", "'''"];

fn next(precedence: u8) -> u8 {
    (precedence + 1).min(ATOM)
}

/// The spelling of `expression`, whose offsets refer to `source`.
pub fn unparse(source: &str, expression: &Expr) -> String {
    let mut writer = Unparser::new(source);
    writer.expr(expression, TEST);
    writer.out
}

/// The spelling of a call keyword, such as `scope=Scope.APP` or `**options`.
pub fn unparse_keyword(source: &str, keyword: &Keyword) -> String {
    let mut writer = Unparser::new(source);
    writer.keyword(keyword);
    writer.out
}

/// The spelling of `values` joined by `and`, as one `BoolOp`.
pub fn unparse_and(source: &str, values: &[Expr]) -> String {
    let mut writer = Unparser::new(source);
    writer.bool_values(BoolOp::And, values, TEST);
    writer.out
}

struct Unparser<'s> {
    source: &'s str,
    out: String,
}

/// One value of a `JoinedStr` after adjacent literals are merged.
enum Part<'a> {
    Constant(String),
    Value(&'a InterpolatedElement),
}

impl<'s> Unparser<'s> {
    fn new(source: &'s str) -> Self {
        Self {
            source,
            out: String::new(),
        }
    }

    fn parens(&mut self, needed: bool, body: impl FnOnce(&mut Self)) {
        if needed {
            self.out.push('(');
        }
        body(self);
        if needed {
            self.out.push(')');
        }
    }

    fn comma_separated<T>(&mut self, items: &[T], mut write: impl FnMut(&mut Self, &T)) {
        for (index, item) in items.iter().enumerate() {
            if index > 0 {
                self.out.push_str(", ");
            }
            write(self, item);
        }
    }

    /// Items with a trailing comma when there is exactly one.
    fn items_view(&mut self, items: &[Expr]) {
        if let [single] = items {
            self.expr(single, TEST);
            self.out.push(',');
        } else {
            self.comma_separated(items, |writer, item| writer.expr(item, TEST));
        }
    }

    #[allow(clippy::too_many_lines)]
    fn expr(&mut self, node: &Expr, precedence: u8) {
        match node {
            Expr::BoolOp(item) => self.bool_values(item.op, &item.values, precedence),
            Expr::Named(item) => self.parens(precedence > NAMED_EXPR, |writer| {
                writer.expr(&item.target, ATOM);
                writer.out.push_str(" := ");
                writer.expr(&item.value, ATOM);
            }),
            Expr::BinOp(item) => {
                let (operator, level) = binary(item.op);
                self.parens(precedence > level, |writer| {
                    let (left, right) = if item.op == Operator::Pow {
                        (next(level), level)
                    } else {
                        (level, next(level))
                    };
                    writer.expr(&item.left, left);
                    writer.out.push(' ');
                    writer.out.push_str(operator);
                    writer.out.push(' ');
                    writer.expr(&item.right, right);
                });
            }
            Expr::UnaryOp(item) => {
                let (operator, level) = match item.op {
                    UnaryOp::Invert => ("~", FACTOR),
                    UnaryOp::Not => ("not", NOT),
                    UnaryOp::UAdd => ("+", FACTOR),
                    UnaryOp::USub => ("-", FACTOR),
                };
                self.parens(precedence > level, |writer| {
                    writer.out.push_str(operator);
                    if level != FACTOR {
                        writer.out.push(' ');
                    }
                    writer.expr(&item.operand, level);
                });
            }
            Expr::Lambda(item) => self.parens(precedence > TEST, |writer| {
                writer.out.push_str("lambda");
                if let Some(parameters) = &item.parameters {
                    let mut inner = Unparser::new(writer.source);
                    inner.parameters(parameters);
                    if !inner.out.is_empty() {
                        writer.out.push(' ');
                        writer.out.push_str(&inner.out);
                    }
                }
                writer.out.push_str(": ");
                writer.expr(&item.body, TEST);
            }),
            Expr::If(item) => self.parens(precedence > TEST, |writer| {
                writer.expr(&item.body, next(TEST));
                writer.out.push_str(" if ");
                writer.expr(&item.test, next(TEST));
                writer.out.push_str(" else ");
                writer.expr(&item.orelse, TEST);
            }),
            Expr::Dict(item) => {
                self.out.push('{');
                self.comma_separated(&item.items, |writer, entry| {
                    if let Some(key) = &entry.key {
                        writer.expr(key, TEST);
                        writer.out.push_str(": ");
                        writer.expr(&entry.value, TEST);
                    } else {
                        writer.out.push_str("**");
                        writer.expr(&entry.value, EXPR);
                    }
                });
                self.out.push('}');
            }
            Expr::Set(item) => {
                self.out.push('{');
                self.comma_separated(&item.elts, |writer, entry| writer.expr(entry, TEST));
                self.out.push('}');
            }
            Expr::ListComp(item) => self.comprehension('[', &item.elt, &item.generators, ']'),
            Expr::SetComp(item) => self.comprehension('{', &item.elt, &item.generators, '}'),
            Expr::Generator(item) => self.comprehension('(', &item.elt, &item.generators, ')'),
            Expr::DictComp(item) => {
                self.out.push('{');
                if let Some(key) = &item.key {
                    self.expr(key, TEST);
                    self.out.push_str(": ");
                    self.expr(&item.value, TEST);
                } else {
                    self.out.push_str("**");
                    self.expr(&item.value, EXPR);
                }
                self.generators(&item.generators);
                self.out.push('}');
            }
            Expr::Await(item) => self.parens(precedence > AWAIT, |writer| {
                writer.out.push_str("await ");
                writer.expr(&item.value, ATOM);
            }),
            Expr::Yield(item) => self.parens(precedence > YIELD, |writer| {
                writer.out.push_str("yield");
                if let Some(value) = &item.value {
                    writer.out.push(' ');
                    writer.expr(value, ATOM);
                }
            }),
            Expr::YieldFrom(item) => self.parens(precedence > YIELD, |writer| {
                writer.out.push_str("yield from ");
                writer.expr(&item.value, ATOM);
            }),
            Expr::Compare(item) => self.parens(precedence > CMP, |writer| {
                writer.expr(&item.operands[0], next(CMP));
                for (operator, value) in item.ops.iter().zip(&item.operands[1..]) {
                    writer.out.push(' ');
                    writer.out.push_str(comparison(*operator));
                    writer.out.push(' ');
                    writer.expr(value, next(CMP));
                }
            }),
            Expr::Call(item) => {
                self.expr(&item.func, ATOM);
                self.out.push('(');
                self.comma_separated(&item.arguments.args, |writer, value| {
                    writer.expr(value, TEST);
                });
                if !item.arguments.args.is_empty() && !item.arguments.keywords.is_empty() {
                    self.out.push_str(", ");
                }
                self.comma_separated(&item.arguments.keywords, Self::keyword);
                self.out.push(')');
            }
            Expr::FString(item) => {
                let parts = fstring_parts(&item.value);
                self.ftstring(&parts, "f", false);
            }
            Expr::TString(item) => {
                let mut parts = Vec::new();
                for template in &item.value {
                    push_elements(&mut parts, &template.elements);
                }
                self.ftstring(&parts, "t", true);
            }
            Expr::StringLiteral(item) => {
                let lowercase_prefix = item.value.iter().next().is_some_and(|literal| {
                    literal.flags.prefix() == StringLiteralPrefix::Unicode
                        && self.source[usize::from(literal.start())..].starts_with('u')
                });
                if lowercase_prefix {
                    self.out.push('u');
                }
                self.out.push_str(&str_repr(item.value.to_str()));
            }
            Expr::BytesLiteral(item) => {
                let bytes: Vec<u8> = item.value.bytes().collect();
                self.out.push_str(&bytes_repr(&bytes));
            }
            Expr::NumberLiteral(item) => match &item.value {
                Number::Int(value) => self.out.push_str(&int_repr(value)),
                Number::Float(value) => {
                    self.out
                        .push_str(&float_repr(*value).replace("inf", INFINITY));
                }
                Number::Complex { imag, .. } => {
                    self.out
                        .push_str(&imaginary_repr(*imag).replace("inf", INFINITY));
                }
            },
            Expr::BooleanLiteral(item) => {
                self.out.push_str(if item.value { "True" } else { "False" });
            }
            Expr::NoneLiteral(_) => self.out.push_str("None"),
            Expr::EllipsisLiteral(_) => self.out.push_str("..."),
            Expr::Attribute(item) => {
                self.expr(&item.value, ATOM);
                let integer = matches!(item.value.as_ref(), Expr::BooleanLiteral(_))
                    || matches!(
                        item.value.as_ref(),
                        Expr::NumberLiteral(number) if matches!(number.value, Number::Int(_))
                    );
                if integer {
                    self.out.push(' ');
                }
                self.out.push('.');
                self.out.push_str(item.attr.as_str());
            }
            Expr::Subscript(item) => {
                self.expr(&item.value, ATOM);
                self.out.push('[');
                match item.slice.as_ref() {
                    Expr::Tuple(tuple) if !tuple.elts.is_empty() => self.items_view(&tuple.elts),
                    other => self.expr(other, TEST),
                }
                self.out.push(']');
            }
            Expr::Starred(item) => {
                self.out.push('*');
                self.expr(&item.value, EXPR);
            }
            Expr::Name(item) => self.out.push_str(item.id.as_str()),
            Expr::List(item) => {
                self.out.push('[');
                self.comma_separated(&item.elts, |writer, entry| writer.expr(entry, TEST));
                self.out.push(']');
            }
            Expr::Tuple(item) => {
                self.parens(item.elts.is_empty() || precedence > TUPLE, |writer| {
                    writer.items_view(&item.elts);
                });
            }
            Expr::Slice(item) => {
                if let Some(lower) = &item.lower {
                    self.expr(lower, TEST);
                }
                self.out.push(':');
                if let Some(upper) = &item.upper {
                    self.expr(upper, TEST);
                }
                if let Some(step) = &item.step {
                    self.out.push(':');
                    self.expr(step, TEST);
                }
            }
            Expr::IpyEscapeCommand(_) => {}
        }
    }

    /// Each operand binds one level tighter than the one before it, as in CPython.
    fn bool_values(&mut self, operator: BoolOp, values: &[Expr], precedence: u8) {
        let (word, level) = match operator {
            BoolOp::And => (" and ", AND),
            BoolOp::Or => (" or ", OR),
        };
        self.parens(precedence > level, |writer| {
            let mut current = level;
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    writer.out.push_str(word);
                }
                current = next(current);
                writer.expr(value, current);
            }
        });
    }

    fn comprehension(
        &mut self,
        open: char,
        element: &Expr,
        generators: &[Comprehension],
        close: char,
    ) {
        self.out.push(open);
        self.expr(element, TEST);
        self.generators(generators);
        self.out.push(close);
    }

    fn generators(&mut self, generators: &[Comprehension]) {
        for generator in generators {
            self.out.push_str(if generator.is_async {
                " async for "
            } else {
                " for "
            });
            self.expr(&generator.target, TUPLE);
            self.out.push_str(" in ");
            self.expr(&generator.iter, next(TEST));
            for condition in &generator.ifs {
                self.out.push_str(" if ");
                self.expr(condition, next(TEST));
            }
        }
    }

    fn keyword(&mut self, keyword: &Keyword) {
        if let Some(name) = &keyword.arg {
            self.out.push_str(name.as_str());
            self.out.push('=');
        } else {
            self.out.push_str("**");
        }
        self.expr(&keyword.value, TEST);
    }

    fn parameter(&mut self, parameter: &Parameter) {
        self.out.push_str(parameter.name.as_str());
        if let Some(annotation) = &parameter.annotation {
            self.out.push_str(": ");
            self.expr(annotation, TEST);
        }
    }

    fn parameters(&mut self, parameters: &Parameters) {
        let mut first = true;
        let positional_only = parameters.posonlyargs.len();
        for (index, item) in parameters
            .posonlyargs
            .iter()
            .chain(&parameters.args)
            .enumerate()
        {
            if !std::mem::take(&mut first) {
                self.out.push_str(", ");
            }
            self.parameter(&item.parameter);
            if let Some(default) = &item.default {
                self.out.push('=');
                self.expr(default, TEST);
            }
            if index + 1 == positional_only {
                self.out.push_str(", /");
            }
        }
        if parameters.vararg.is_some() || !parameters.kwonlyargs.is_empty() {
            if !std::mem::take(&mut first) {
                self.out.push_str(", ");
            }
            self.out.push('*');
            if let Some(variadic) = &parameters.vararg {
                self.parameter(variadic);
            }
        }
        for item in &parameters.kwonlyargs {
            self.out.push_str(", ");
            self.parameter(&item.parameter);
            if let Some(default) = &item.default {
                self.out.push('=');
                self.expr(default, TEST);
            }
        }
        if let Some(keywords) = &parameters.kwarg {
            if !std::mem::take(&mut first) {
                self.out.push_str(", ");
            }
            self.out.push_str("**");
            self.parameter(keywords);
        }
    }

    fn ftstring(&mut self, parts: &[Part<'_>], prefix: &str, template: bool) {
        self.out.push_str(prefix);
        let rendered: Vec<(String, bool)> = parts
            .iter()
            .map(|part| match part {
                Part::Constant(value) => (value.replace('{', "{{").replace('}', "}}"), true),
                Part::Value(element) => (self.interpolation(element, template), false),
            })
            .collect();
        let (text, quote) = join_parts(&rendered);
        self.out.push_str(quote);
        self.out.push_str(&text);
        self.out.push_str(quote);
    }

    fn interpolation(&self, element: &InterpolatedElement, template: bool) -> String {
        let mut out = String::from("{");
        let text = if template {
            template_text(self.source, element)
        } else {
            let mut inner = Unparser::new(self.source);
            inner.expr(&element.expression, next(TEST));
            inner.out
        };
        if text.starts_with('{') {
            out.push(' ');
        }
        out.push_str(&text);
        let conversion = match element.conversion {
            ConversionFlag::Str => Some('s'),
            ConversionFlag::Ascii => Some('a'),
            ConversionFlag::Repr => Some('r'),
            // `f"{x=}"` implies repr unless a format spec is given.
            ConversionFlag::None => {
                (element.debug_text.is_some() && element.format_spec.is_none()).then_some('r')
            }
        };
        if let Some(conversion) = conversion {
            out.push('!');
            out.push(conversion);
        }
        if let Some(spec) = &element.format_spec {
            out.push(':');
            let mut parts = Vec::new();
            push_elements(&mut parts, &spec.elements);
            for part in &parts {
                match part {
                    Part::Constant(value) => {
                        let escaped = value
                            .replace('{', "{{")
                            .replace('}', "}}")
                            .replace('\\', "\\\\")
                            .replace('\'', "\\'")
                            .replace('"', "\\\"")
                            .replace('\n', "\\n");
                        out.push_str(&escaped);
                    }
                    Part::Value(inner) => out.push_str(&self.interpolation(inner, template)),
                }
            }
        }
        out.push('}');
        out
    }
}

/// The expression of a template interpolation as written, like `Interpolation.str`.
fn template_text(source: &str, element: &InterpolatedElement) -> String {
    let start = usize::from(element.start()) + 1;
    let mut end = usize::from(element.expression.end());
    let tail = end;
    for (offset, character) in source[tail..].char_indices() {
        if character == ')' {
            end = tail + offset + 1;
        } else if !character.is_whitespace() {
            break;
        }
    }
    source[start..end].to_owned()
}

fn fstring_parts(value: &FStringValue) -> Vec<Part<'_>> {
    let mut parts = Vec::new();
    for part in value {
        match part {
            FStringPartRef::Literal(literal) => push_constant(&mut parts, &literal.value),
            FStringPartRef::FString(fstring) => push_elements(&mut parts, &fstring.elements),
        }
    }
    parts
}

fn push_elements<'a>(parts: &mut Vec<Part<'a>>, elements: &'a InterpolatedStringElements) {
    for element in elements {
        match element {
            InterpolatedStringElement::Literal(literal) => push_constant(parts, &literal.value),
            InterpolatedStringElement::Interpolation(value) => {
                if let Some(debug) = &value.debug_text {
                    push_constant(parts, debug.as_str());
                }
                parts.push(Part::Value(value));
            }
        }
    }
}

fn push_constant(parts: &mut Vec<Part<'_>>, value: &str) {
    if value.is_empty() {
        return;
    }
    if let Some(Part::Constant(previous)) = parts.last_mut() {
        previous.push_str(value);
    } else {
        parts.push(Part::Constant(value.to_owned()));
    }
}

/// Join rendered parts under one quote style, like `_ftstring_helper`.
fn join_parts(parts: &[(String, bool)]) -> (String, &'static str) {
    let mut quotes: Vec<&'static str> = ALL_QUOTES.to_vec();
    let mut joined = String::new();
    let mut fallback = false;
    for (value, is_constant) in parts {
        if *is_constant {
            let (escaped, updated) = escape_literal(value, &quotes);
            if updated.iter().all(|quote| !quotes.contains(quote)) {
                fallback = true;
                break;
            }
            quotes = updated;
            joined.push_str(&escaped);
        } else {
            if value.contains('\n') {
                quotes.retain(|quote| quote.len() == 3);
            }
            let remaining: Vec<&'static str> = quotes
                .iter()
                .copied()
                .filter(|quote| !value.contains(quote))
                .collect();
            if !remaining.is_empty() {
                quotes = remaining;
            }
            joined.push_str(value);
        }
    }
    if fallback {
        joined.clear();
        for (value, is_constant) in parts {
            if *is_constant {
                // A leading double quote forces repr to use single quotes.
                let rendered = str_repr(&format!("\"{value}"));
                joined.push_str(&rendered[2..rendered.len() - 1]);
            } else {
                joined.push_str(value);
            }
        }
        return (joined, "'''");
    }
    (joined, quotes[0])
}

/// `_str_literal_helper` with `escape_special_whitespace=True`.
fn escape_literal(text: &str, quotes: &[&'static str]) -> (String, Vec<&'static str>) {
    let mut escaped = String::with_capacity(text.len());
    for character in text.chars() {
        if character == '\\' || !is_printable(character) {
            escaped.push_str(&unicode_escape(character));
        } else {
            escaped.push(character);
        }
    }
    let mut possible: Vec<&'static str> = quotes
        .iter()
        .copied()
        .filter(|quote| !escaped.contains(quote))
        .collect();
    if possible.is_empty() {
        let rendered = str_repr(text);
        let first = &rendered[..1];
        let quote = quotes
            .iter()
            .copied()
            .find(|quote| quote.contains(first))
            .unwrap_or(if first == "'" { "'" } else { "\"" });
        return (rendered[1..rendered.len() - 1].to_owned(), vec![quote]);
    }
    if let Some(last) = escaped.chars().next_back() {
        possible.sort_by_key(|quote| quote.starts_with(last));
        if possible[0].starts_with(last) {
            escaped.pop();
            escaped.push('\\');
            escaped.push(last);
        }
    }
    (escaped, possible)
}

fn binary(operator: Operator) -> (&'static str, u8) {
    match operator {
        Operator::Add => ("+", ARITH),
        Operator::Sub => ("-", ARITH),
        Operator::Mult => ("*", TERM),
        Operator::MatMult => ("@", TERM),
        Operator::Div => ("/", TERM),
        Operator::Mod => ("%", TERM),
        Operator::FloorDiv => ("//", TERM),
        Operator::LShift => ("<<", SHIFT),
        Operator::RShift => (">>", SHIFT),
        Operator::BitOr => ("|", EXPR),
        Operator::BitXor => ("^", BXOR),
        Operator::BitAnd => ("&", BAND),
        Operator::Pow => ("**", POWER),
    }
}

fn comparison(operator: CmpOp) -> &'static str {
    match operator {
        CmpOp::Eq => "==",
        CmpOp::NotEq => "!=",
        CmpOp::Lt => "<",
        CmpOp::LtE => "<=",
        CmpOp::Gt => ">",
        CmpOp::GtE => ">=",
        CmpOp::Is => "is",
        CmpOp::IsNot => "is not",
        CmpOp::In => "in",
        CmpOp::NotIn => "not in",
    }
}
