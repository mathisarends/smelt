//! `ast.dump` with default options, as Python 3.13 and later print it.
//!
//! Debt entries of several rules key on a digest of this text, so it has to stay
//! byte-identical to CPython's.
use super::repr::{bytes_repr, float_repr, imaginary_repr, int_repr, str_repr};
use super::unparse::template_text;
use ruff_python_ast::str_prefix::StringLiteralPrefix;
use ruff_python_ast::{
    Alias, BoolOp, CmpOp, Comprehension, ConversionFlag, ElifElseClause, ExceptHandler, Expr,
    ExprContext, ExprFString, ExprTString, FStringPartRef, InterpolatedElement,
    InterpolatedStringElement, InterpolatedStringElements, Keyword, MatchCase, Number, Operator,
    Parameter, Parameters, Pattern, Singleton, Stmt, StringLiteral, TypeParam, TypeParams, UnaryOp,
    WithItem,
};
use ruff_text_size::Ranged;
use sha2::{Digest, Sha256};
use std::fmt::Write;

/// `ast.dump(expression)`.
pub fn dump_expr(source: &str, expression: &Expr) -> String {
    let mut writer = Dumper::new(source);
    writer.expr(expression);
    writer.out
}

/// `ast.dump(statement)`.
pub fn dump_stmt(source: &str, statement: &Stmt) -> String {
    let mut writer = Dumper::new(source);
    writer.stmt(statement);
    writer.out
}

/// `ast.dump` of the `If` node CPython builds for an `elif` and what follows it.
pub fn dump_elif(source: &str, test: &Expr, body: &[Stmt], rest: &[ElifElseClause]) -> String {
    let mut writer = Dumper::new(source);
    writer.if_chain(test, body, rest);
    writer.out
}

/// The lowercase SHA-256 of `text`.
pub fn sha256_hex(text: &str) -> String {
    let digest = Sha256::digest(text.as_bytes());
    let mut out = String::with_capacity(64);
    for byte in digest {
        write!(out, "{byte:02x}").expect("writing to a string cannot fail");
    }
    out
}

struct Dumper<'s> {
    source: &'s str,
    out: String,
    /// The current node has no field yet, so the next one needs no separator.
    fresh: bool,
}

/// One value of a `JoinedStr` or `TemplateStr`.
enum Piece<'a> {
    Constant { value: String, unicode: bool },
    Value(&'a InterpolatedElement),
}

impl<'s> Dumper<'s> {
    fn new(source: &'s str) -> Self {
        Self {
            source,
            out: String::new(),
            fresh: false,
        }
    }

    fn open(&mut self, kind: &str) {
        self.out.push_str(kind);
        self.out.push('(');
        self.fresh = true;
    }

    fn close(&mut self) {
        self.out.push(')');
        self.fresh = false;
    }

    fn name(&mut self, field: &str) {
        if !std::mem::take(&mut self.fresh) {
            self.out.push_str(", ");
        }
        self.out.push_str(field);
        self.out.push('=');
    }

    fn raw(&mut self, field: &str, value: &str) {
        self.name(field);
        self.out.push_str(value);
    }

    fn text(&mut self, field: &str, value: &str) {
        self.raw(field, &str_repr(value));
    }

    fn value(&mut self, field: &str, value: &Expr) {
        self.name(field);
        self.expr(value);
    }

    fn optional(&mut self, field: &str, value: Option<&Expr>) {
        if let Some(value) = value {
            self.value(field, value);
        }
    }

    /// Empty lists are left out, as `show_empty=False` does.
    fn list<T>(&mut self, field: &str, items: &[T], mut write: impl FnMut(&mut Self, &T)) {
        if items.is_empty() {
            return;
        }
        self.name(field);
        self.out.push('[');
        for (index, item) in items.iter().enumerate() {
            if index > 0 {
                self.out.push_str(", ");
            }
            write(self, item);
        }
        self.out.push(']');
        self.fresh = false;
    }

    fn exprs(&mut self, field: &str, items: &[Expr]) {
        self.list(field, items, Self::expr);
    }

    fn body(&mut self, field: &str, items: &[Stmt]) {
        self.list(field, items, Self::stmt);
    }

    fn context(&mut self, context: ExprContext) {
        self.raw(
            "ctx",
            match context {
                ExprContext::Store => "Store()",
                ExprContext::Del => "Del()",
                ExprContext::Load | ExprContext::Invalid => "Load()",
            },
        );
    }

    #[allow(clippy::too_many_lines)]
    fn expr(&mut self, node: &Expr) {
        match node {
            Expr::BoolOp(item) => {
                self.open("BoolOp");
                self.raw(
                    "op",
                    match item.op {
                        BoolOp::And => "And()",
                        BoolOp::Or => "Or()",
                    },
                );
                self.exprs("values", &item.values);
                self.close();
            }
            Expr::Named(item) => {
                self.open("NamedExpr");
                self.value("target", &item.target);
                self.value("value", &item.value);
                self.close();
            }
            Expr::BinOp(item) => {
                self.open("BinOp");
                self.value("left", &item.left);
                self.raw("op", operator(item.op));
                self.value("right", &item.right);
                self.close();
            }
            Expr::UnaryOp(item) => {
                self.open("UnaryOp");
                self.raw(
                    "op",
                    match item.op {
                        UnaryOp::Invert => "Invert()",
                        UnaryOp::Not => "Not()",
                        UnaryOp::UAdd => "UAdd()",
                        UnaryOp::USub => "USub()",
                    },
                );
                self.value("operand", &item.operand);
                self.close();
            }
            Expr::Lambda(item) => {
                self.open("Lambda");
                self.name("args");
                match &item.parameters {
                    Some(parameters) => self.parameters(parameters),
                    None => self.out.push_str("arguments()"),
                }
                self.value("body", &item.body);
                self.close();
            }
            Expr::If(item) => {
                self.open("IfExp");
                self.value("test", &item.test);
                self.value("body", &item.body);
                self.value("orelse", &item.orelse);
                self.close();
            }
            Expr::Dict(item) => {
                self.open("Dict");
                self.list("keys", &item.items, |writer, entry| match &entry.key {
                    Some(key) => writer.expr(key),
                    None => writer.out.push_str("None"),
                });
                self.list("values", &item.items, |writer, entry| {
                    writer.expr(&entry.value);
                });
                self.close();
            }
            Expr::Set(item) => {
                self.open("Set");
                self.exprs("elts", &item.elts);
                self.close();
            }
            Expr::ListComp(item) => self.comprehension("ListComp", &item.elt, &item.generators),
            Expr::SetComp(item) => self.comprehension("SetComp", &item.elt, &item.generators),
            Expr::Generator(item) => {
                self.comprehension("GeneratorExp", &item.elt, &item.generators);
            }
            Expr::DictComp(item) => {
                self.open("DictComp");
                self.optional("key", item.key.as_deref());
                self.value("value", &item.value);
                self.list("generators", &item.generators, Self::generator);
                self.close();
            }
            Expr::Await(item) => {
                self.open("Await");
                self.value("value", &item.value);
                self.close();
            }
            Expr::Yield(item) => {
                self.open("Yield");
                self.optional("value", item.value.as_deref());
                self.close();
            }
            Expr::YieldFrom(item) => {
                self.open("YieldFrom");
                self.value("value", &item.value);
                self.close();
            }
            Expr::Compare(item) => {
                self.open("Compare");
                self.value("left", &item.operands[0]);
                self.list("ops", &item.ops, |writer, op| {
                    writer.out.push_str(comparison(*op));
                });
                self.exprs("comparators", &item.operands[1..]);
                self.close();
            }
            Expr::Call(item) => {
                self.open("Call");
                self.value("func", &item.func);
                self.exprs("args", &item.arguments.args);
                self.list("keywords", &item.arguments.keywords, Self::keyword);
                self.close();
            }
            Expr::FString(item) => {
                let pieces = fstring_pieces(self.source, item);
                self.joined("JoinedStr", pieces, false);
            }
            Expr::TString(item) => {
                let pieces = template_pieces(item);
                self.joined("TemplateStr", pieces, true);
            }
            Expr::StringLiteral(item) => {
                let unicode = item
                    .value
                    .iter()
                    .next()
                    .is_some_and(|literal| is_unicode_literal(self.source, literal));
                self.string_constant(item.value.to_str(), unicode);
            }
            Expr::BytesLiteral(item) => {
                let bytes: Vec<u8> = item.value.bytes().collect();
                self.constant(&bytes_repr(&bytes));
            }
            Expr::NumberLiteral(item) => {
                let text = match &item.value {
                    Number::Int(value) => int_repr(value),
                    Number::Float(value) => float_repr(*value),
                    Number::Complex { imag, .. } => imaginary_repr(*imag),
                };
                self.constant(&text);
            }
            Expr::BooleanLiteral(item) => {
                self.constant(if item.value { "True" } else { "False" });
            }
            Expr::NoneLiteral(_) => self.constant("None"),
            Expr::EllipsisLiteral(_) => self.constant("Ellipsis"),
            Expr::Attribute(item) => {
                self.open("Attribute");
                self.value("value", &item.value);
                self.text("attr", item.attr.as_str());
                self.context(item.ctx);
                self.close();
            }
            Expr::Subscript(item) => {
                self.open("Subscript");
                self.value("value", &item.value);
                self.value("slice", &item.slice);
                self.context(item.ctx);
                self.close();
            }
            Expr::Starred(item) => {
                self.open("Starred");
                self.value("value", &item.value);
                self.context(item.ctx);
                self.close();
            }
            Expr::Name(item) => {
                self.open("Name");
                self.text("id", item.id.as_str());
                self.context(item.ctx);
                self.close();
            }
            Expr::List(item) => {
                self.open("List");
                self.exprs("elts", &item.elts);
                self.context(item.ctx);
                self.close();
            }
            Expr::Tuple(item) => {
                self.open("Tuple");
                self.exprs("elts", &item.elts);
                self.context(item.ctx);
                self.close();
            }
            Expr::Slice(item) => {
                self.open("Slice");
                self.optional("lower", item.lower.as_deref());
                self.optional("upper", item.upper.as_deref());
                self.optional("step", item.step.as_deref());
                self.close();
            }
            Expr::IpyEscapeCommand(_) => {}
        }
    }

    fn constant(&mut self, value: &str) {
        self.open("Constant");
        self.raw("value", value);
        self.close();
    }

    fn string_constant(&mut self, value: &str, unicode: bool) {
        self.open("Constant");
        self.text("value", value);
        if unicode {
            self.raw("kind", "'u'");
        }
        self.close();
    }

    fn comprehension(&mut self, kind: &str, element: &Expr, generators: &[Comprehension]) {
        self.open(kind);
        self.value("elt", element);
        self.list("generators", generators, Self::generator);
        self.close();
    }

    fn generator(&mut self, generator: &Comprehension) {
        self.open("comprehension");
        self.value("target", &generator.target);
        self.value("iter", &generator.iter);
        self.exprs("ifs", &generator.ifs);
        self.raw("is_async", if generator.is_async { "1" } else { "0" });
        self.close();
    }

    fn keyword(&mut self, keyword: &Keyword) {
        self.open("keyword");
        if let Some(name) = &keyword.arg {
            self.text("arg", name.as_str());
        }
        self.value("value", &keyword.value);
        self.close();
    }

    fn joined(&mut self, kind: &str, pieces: Vec<Piece<'_>>, template: bool) {
        let merged = merge_pieces(pieces);
        self.open(kind);
        self.list("values", &merged, |writer, piece| match piece {
            Piece::Constant { value, unicode } => writer.string_constant(value, *unicode),
            Piece::Value(element) => writer.formatted(element, template),
        });
        self.close();
    }

    fn formatted(&mut self, element: &InterpolatedElement, template: bool) {
        self.open(if template {
            "Interpolation"
        } else {
            "FormattedValue"
        });
        self.value("value", &element.expression);
        if template {
            self.text("str", &template_text(self.source, element));
        }
        let conversion = match element.conversion {
            ConversionFlag::Str => "115",
            ConversionFlag::Ascii => "97",
            ConversionFlag::Repr => "114",
            // `f"{x=}"` implies repr unless a format spec is given.
            ConversionFlag::None => {
                if element.debug_text.is_some() && element.format_spec.is_none() {
                    "114"
                } else {
                    "-1"
                }
            }
        };
        self.raw("conversion", conversion);
        if let Some(spec) = &element.format_spec {
            self.name("format_spec");
            let mut pieces = Vec::new();
            push_elements(&mut pieces, &spec.elements);
            self.joined("JoinedStr", pieces, false);
        }
        self.close();
    }

    fn parameter(&mut self, parameter: &Parameter) {
        self.open("arg");
        self.text("arg", parameter.name.as_str());
        self.optional("annotation", parameter.annotation.as_deref());
        self.close();
    }

    fn parameters(&mut self, parameters: &Parameters) {
        self.open("arguments");
        self.list("posonlyargs", &parameters.posonlyargs, |writer, item| {
            writer.parameter(&item.parameter);
        });
        self.list("args", &parameters.args, |writer, item| {
            writer.parameter(&item.parameter);
        });
        if let Some(variadic) = &parameters.vararg {
            self.name("vararg");
            self.parameter(variadic);
        }
        self.list("kwonlyargs", &parameters.kwonlyargs, |writer, item| {
            writer.parameter(&item.parameter);
        });
        self.list(
            "kw_defaults",
            &parameters.kwonlyargs,
            |writer, item| match &item.default {
                Some(default) => writer.expr(default),
                None => writer.out.push_str("None"),
            },
        );
        if let Some(keywords) = &parameters.kwarg {
            self.name("kwarg");
            self.parameter(keywords);
        }
        let defaults: Vec<&Expr> = parameters
            .posonlyargs
            .iter()
            .chain(&parameters.args)
            .filter_map(|item| item.default.as_deref())
            .collect();
        self.list("defaults", &defaults, |writer, default| {
            writer.expr(default)
        });
        self.close();
    }

    fn type_params(&mut self, parameters: Option<&TypeParams>) {
        if let Some(parameters) = parameters {
            self.list("type_params", &parameters.type_params, Self::type_param);
        }
    }

    fn type_param(&mut self, parameter: &TypeParam) {
        match parameter {
            TypeParam::TypeVar(item) => {
                self.open("TypeVar");
                self.text("name", item.name.as_str());
                self.optional("bound", item.bound.as_deref());
                self.optional("default_value", item.default.as_deref());
            }
            TypeParam::ParamSpec(item) => {
                self.open("ParamSpec");
                self.text("name", item.name.as_str());
                self.optional("default_value", item.default.as_deref());
            }
            TypeParam::TypeVarTuple(item) => {
                self.open("TypeVarTuple");
                self.text("name", item.name.as_str());
                self.optional("default_value", item.default.as_deref());
            }
        }
        self.close();
    }

    fn decorators(&mut self, decorators: &[ruff_python_ast::Decorator]) {
        self.list("decorator_list", decorators, |writer, decorator| {
            writer.expr(&decorator.expression);
        });
    }

    fn if_chain(&mut self, test: &Expr, body: &[Stmt], clauses: &[ElifElseClause]) {
        self.open("If");
        self.value("test", test);
        self.body("body", body);
        if let Some((clause, rest)) = clauses.split_first() {
            match &clause.test {
                Some(next) => {
                    self.name("orelse");
                    self.out.push('[');
                    self.if_chain(next, &clause.body, rest);
                    self.out.push(']');
                }
                None => self.body("orelse", &clause.body),
            }
        }
        self.close();
    }

    #[allow(clippy::too_many_lines)]
    fn stmt(&mut self, node: &Stmt) {
        match node {
            Stmt::FunctionDef(item) => {
                self.open(if item.is_async {
                    "AsyncFunctionDef"
                } else {
                    "FunctionDef"
                });
                self.text("name", item.name.as_str());
                self.name("args");
                self.parameters(&item.parameters);
                self.body("body", &item.body);
                self.decorators(&item.decorator_list);
                self.optional("returns", item.returns.as_deref());
                self.type_params(item.type_params.as_deref());
                self.close();
            }
            Stmt::ClassDef(item) => {
                let (bases, keywords): (&[Expr], &[Keyword]) = match &item.arguments {
                    Some(arguments) => (&arguments.args, &arguments.keywords),
                    None => (&[], &[]),
                };
                self.open("ClassDef");
                self.text("name", item.name.as_str());
                self.exprs("bases", bases);
                self.list("keywords", keywords, Self::keyword);
                self.body("body", &item.body);
                self.decorators(&item.decorator_list);
                self.type_params(item.type_params.as_deref());
                self.close();
            }
            Stmt::Return(item) => {
                self.open("Return");
                self.optional("value", item.value.as_deref());
                self.close();
            }
            Stmt::Delete(item) => {
                self.open("Delete");
                self.exprs("targets", &item.targets);
                self.close();
            }
            Stmt::TypeAlias(item) => {
                self.open("TypeAlias");
                self.value("name", &item.name);
                self.type_params(item.type_params.as_deref());
                self.value("value", &item.value);
                self.close();
            }
            Stmt::Assign(item) => {
                self.open("Assign");
                self.exprs("targets", &item.targets);
                self.value("value", &item.value);
                self.close();
            }
            Stmt::AugAssign(item) => {
                self.open("AugAssign");
                self.value("target", &item.target);
                self.raw("op", operator(item.op));
                self.value("value", &item.value);
                self.close();
            }
            Stmt::AnnAssign(item) => {
                self.open("AnnAssign");
                self.value("target", &item.target);
                self.value("annotation", &item.annotation);
                self.optional("value", item.value.as_deref());
                self.raw("simple", if item.simple { "1" } else { "0" });
                self.close();
            }
            Stmt::For(item) => {
                self.open(if item.is_async { "AsyncFor" } else { "For" });
                self.value("target", &item.target);
                self.value("iter", &item.iter);
                self.body("body", &item.body);
                self.body("orelse", &item.orelse);
                self.close();
            }
            Stmt::While(item) => {
                self.open("While");
                self.value("test", &item.test);
                self.body("body", &item.body);
                self.body("orelse", &item.orelse);
                self.close();
            }
            Stmt::If(item) => self.if_chain(&item.test, &item.body, &item.elif_else_clauses),
            Stmt::With(item) => {
                self.open(if item.is_async { "AsyncWith" } else { "With" });
                self.list("items", &item.items, Self::with_item);
                self.body("body", &item.body);
                self.close();
            }
            Stmt::Match(item) => {
                self.open("Match");
                self.value("subject", &item.subject);
                self.list("cases", &item.cases, Self::match_case);
                self.close();
            }
            Stmt::Raise(item) => {
                self.open("Raise");
                self.optional("exc", item.exc.as_deref());
                self.optional("cause", item.cause.as_deref());
                self.close();
            }
            Stmt::Try(item) => {
                self.open(if item.is_star { "TryStar" } else { "Try" });
                self.body("body", &item.body);
                self.list("handlers", &item.handlers, Self::handler);
                self.body("orelse", &item.orelse);
                self.body("finalbody", &item.finalbody);
                self.close();
            }
            Stmt::Assert(item) => {
                self.open("Assert");
                self.value("test", &item.test);
                self.optional("msg", item.msg.as_deref());
                self.close();
            }
            Stmt::Import(item) => {
                self.open("Import");
                self.list("names", &item.names, Self::alias);
                self.close();
            }
            Stmt::ImportFrom(item) => {
                self.open("ImportFrom");
                if let Some(module) = &item.module {
                    self.text("module", module.as_str());
                }
                self.list("names", &item.names, Self::alias);
                self.raw("level", &item.level.to_string());
                self.close();
            }
            Stmt::Global(item) => {
                self.open("Global");
                self.list("names", &item.names, |writer, name| {
                    writer.out.push_str(&str_repr(name.as_str()));
                });
                self.close();
            }
            Stmt::Nonlocal(item) => {
                self.open("Nonlocal");
                self.list("names", &item.names, |writer, name| {
                    writer.out.push_str(&str_repr(name.as_str()));
                });
                self.close();
            }
            Stmt::Expr(item) => {
                self.open("Expr");
                self.value("value", &item.value);
                self.close();
            }
            Stmt::Pass(_) => {
                self.open("Pass");
                self.close();
            }
            Stmt::Break(_) => {
                self.open("Break");
                self.close();
            }
            Stmt::Continue(_) => {
                self.open("Continue");
                self.close();
            }
            Stmt::IpyEscapeCommand(_) => {}
        }
    }

    fn alias(&mut self, alias: &Alias) {
        self.open("alias");
        self.text("name", alias.name.as_str());
        if let Some(name) = &alias.asname {
            self.text("asname", name.as_str());
        }
        self.close();
    }

    fn with_item(&mut self, item: &WithItem) {
        self.open("withitem");
        self.value("context_expr", &item.context_expr);
        self.optional("optional_vars", item.optional_vars.as_deref());
        self.close();
    }

    fn handler(&mut self, handler: &ExceptHandler) {
        let ExceptHandler::ExceptHandler(item) = handler;
        self.open("ExceptHandler");
        self.optional("type", item.type_.as_deref());
        if let Some(name) = &item.name {
            self.text("name", name.as_str());
        }
        self.body("body", &item.body);
        self.close();
    }

    fn match_case(&mut self, case: &MatchCase) {
        self.open("match_case");
        self.name("pattern");
        self.pattern(&case.pattern);
        self.optional("guard", case.guard.as_deref());
        self.body("body", &case.body);
        self.close();
    }

    fn patterns(&mut self, field: &str, patterns: &[Pattern]) {
        self.list(field, patterns, Self::pattern);
    }

    fn pattern(&mut self, pattern: &Pattern) {
        match pattern {
            Pattern::MatchValue(item) => {
                self.open("MatchValue");
                self.value("value", &item.value);
            }
            Pattern::MatchSingleton(item) => {
                self.open("MatchSingleton");
                self.raw(
                    "value",
                    match item.value {
                        Singleton::None => "None",
                        Singleton::True => "True",
                        Singleton::False => "False",
                    },
                );
            }
            Pattern::MatchSequence(item) => {
                self.open("MatchSequence");
                self.patterns("patterns", &item.patterns);
            }
            Pattern::MatchMapping(item) => {
                self.open("MatchMapping");
                self.exprs("keys", &item.keys);
                self.patterns("patterns", &item.patterns);
                if let Some(rest) = &item.rest {
                    self.text("rest", rest.as_str());
                }
            }
            Pattern::MatchClass(item) => {
                self.open("MatchClass");
                self.value("cls", &item.cls);
                self.patterns("patterns", &item.arguments.patterns);
                self.list("kwd_attrs", &item.arguments.keywords, |writer, keyword| {
                    writer.out.push_str(&str_repr(keyword.attr.as_str()));
                });
                self.list(
                    "kwd_patterns",
                    &item.arguments.keywords,
                    |writer, keyword| writer.pattern(&keyword.pattern),
                );
            }
            Pattern::MatchStar(item) => {
                self.open("MatchStar");
                if let Some(name) = &item.name {
                    self.text("name", name.as_str());
                }
            }
            Pattern::MatchAs(item) => {
                self.open("MatchAs");
                if let Some(inner) = &item.pattern {
                    self.name("pattern");
                    self.pattern(inner);
                }
                if let Some(name) = &item.name {
                    self.text("name", name.as_str());
                }
            }
            Pattern::MatchOr(item) => {
                self.open("MatchOr");
                self.patterns("patterns", &item.patterns);
            }
        }
        self.close();
    }
}

/// Only a lowercase `u` prefix sets `Constant.kind`.
fn is_unicode_literal(source: &str, literal: &StringLiteral) -> bool {
    literal.flags.prefix() == StringLiteralPrefix::Unicode
        && source[usize::from(literal.start())..].starts_with('u')
}

/// The `ast.Constant` strings of an f-string or t-string, including its format specs.
pub fn joined_constants(source: &str, expression: &Expr) -> Vec<String> {
    let pieces = match expression {
        Expr::FString(item) => fstring_pieces(source, item),
        Expr::TString(item) => template_pieces(item),
        _ => Vec::new(),
    };
    let mut found = Vec::new();
    collect_constants(pieces, &mut found);
    found
}

fn collect_constants(pieces: Vec<Piece<'_>>, found: &mut Vec<String>) {
    for piece in merge_pieces(pieces) {
        match piece {
            Piece::Constant { value, .. } => found.push(value),
            Piece::Value(element) => {
                if let Some(spec) = &element.format_spec {
                    let mut nested = Vec::new();
                    push_elements(&mut nested, &spec.elements);
                    collect_constants(nested, found);
                }
            }
        }
    }
}

fn fstring_pieces<'a>(source: &str, item: &'a ExprFString) -> Vec<Piece<'a>> {
    let mut pieces = Vec::new();
    for part in &item.value {
        match part {
            FStringPartRef::Literal(literal) => pieces.push(Piece::Constant {
                value: literal.value.to_string(),
                unicode: is_unicode_literal(source, literal),
            }),
            FStringPartRef::FString(fstring) => push_elements(&mut pieces, &fstring.elements),
        }
    }
    pieces
}

fn template_pieces(item: &ExprTString) -> Vec<Piece<'_>> {
    let mut pieces = Vec::new();
    for template in &item.value {
        push_elements(&mut pieces, &template.elements);
    }
    pieces
}

fn push_elements<'a>(pieces: &mut Vec<Piece<'a>>, elements: &'a InterpolatedStringElements) {
    for element in elements {
        match element {
            InterpolatedStringElement::Literal(literal) => pieces.push(Piece::Constant {
                value: literal.value.to_string(),
                unicode: false,
            }),
            InterpolatedStringElement::Interpolation(value) => {
                if let Some(debug) = &value.debug_text {
                    pieces.push(Piece::Constant {
                        value: debug.as_str().to_owned(),
                        unicode: false,
                    });
                }
                pieces.push(Piece::Value(value));
            }
        }
    }
}

/// Fold adjacent constants: the first decides `kind`, even when it is empty.
fn merge_pieces(pieces: Vec<Piece<'_>>) -> Vec<Piece<'_>> {
    let mut merged: Vec<Piece<'_>> = Vec::with_capacity(pieces.len());
    for piece in pieces {
        match (merged.last_mut(), piece) {
            (
                Some(Piece::Constant {
                    value: previous, ..
                }),
                Piece::Constant { value, .. },
            ) => {
                previous.push_str(&value);
            }
            (_, piece) => merged.push(piece),
        }
    }
    merged.retain(|piece| !matches!(piece, Piece::Constant { value, .. } if value.is_empty()));
    merged
}

fn operator(operator: Operator) -> &'static str {
    match operator {
        Operator::Add => "Add()",
        Operator::Sub => "Sub()",
        Operator::Mult => "Mult()",
        Operator::MatMult => "MatMult()",
        Operator::Div => "Div()",
        Operator::Mod => "Mod()",
        Operator::Pow => "Pow()",
        Operator::LShift => "LShift()",
        Operator::RShift => "RShift()",
        Operator::BitOr => "BitOr()",
        Operator::BitXor => "BitXor()",
        Operator::BitAnd => "BitAnd()",
        Operator::FloorDiv => "FloorDiv()",
    }
}

fn comparison(operator: CmpOp) -> &'static str {
    match operator {
        CmpOp::Eq => "Eq()",
        CmpOp::NotEq => "NotEq()",
        CmpOp::Lt => "Lt()",
        CmpOp::LtE => "LtE()",
        CmpOp::Gt => "Gt()",
        CmpOp::GtE => "GtE()",
        CmpOp::Is => "Is()",
        CmpOp::IsNot => "IsNot()",
        CmpOp::In => "In()",
        CmpOp::NotIn => "NotIn()",
    }
}
