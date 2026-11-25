//! One parsed source with CPython-compatible positions.
use super::files::SourceFile;
use super::vfs;
use ruff_python_ast::token::TokenKind;
use ruff_python_ast::{Expr, ModModule, Stmt, StmtClassDef, StmtFunctionDef};
use ruff_python_parser::{Mode, ParseOptions, Parsed, parse_unchecked};
use ruff_text_size::{Ranged, TextRange, TextSize};
use serde_json::{Value, json};

pub struct SourceText {
    pub text: String,
    starts: Vec<usize>,
}

impl SourceText {
    fn new(text: String) -> Self {
        let mut starts = vec![0];
        starts.extend(text.match_indices('\n').map(|(index, _)| index + 1));
        Self { text, starts }
    }

    /// The 1-based line and 0-based UTF-8 column of a byte offset.
    pub fn position(&self, offset: usize) -> (usize, usize) {
        let line = self.starts.partition_point(|start| *start <= offset);
        (line, offset - self.starts[line - 1])
    }

    /// The offset where the 1-based `line` starts.
    pub fn line_start(&self, line: usize) -> usize {
        self.starts[line - 1]
    }

    /// The offset after the newline that ends `line`, or the end of the text.
    pub fn line_end(&self, line: usize) -> usize {
        self.starts.get(line).copied().unwrap_or(self.text.len())
    }

    /// The 1-based line that contains `offset`.
    pub fn line_at(&self, offset: usize) -> usize {
        self.position(offset).0
    }

    /// A `TextEdit` as JSON: 1-based lines and 1-based UTF-8 columns.
    pub fn edit(&self, start: usize, end: usize, replacement: &str) -> Value {
        let (line, column) = self.position(start);
        let (end_line, end_column) = self.position(end);
        json!({
            "line": line,
            "column": column + 1,
            "end_line": end_line,
            "end_column": end_column + 1,
            "original": &self.text[start..end],
            "replacement": replacement,
        })
    }
}

pub struct ParsedSource {
    pub text: SourceText,
    pub parsed: Parsed<ModModule>,
    /// Why the source is not valid Python; only a tolerant read keeps such a source.
    pub syntax_error: Option<String>,
}

impl ParsedSource {
    pub fn read(source: &SourceFile) -> Result<Self, String> {
        let parsed = Self::read_tolerant(source)?;
        match parsed.syntax_error {
            Some(error) => Err(error),
            None => Ok(parsed),
        }
    }

    /// Read a source that may not parse, recovering what the parser can.
    ///
    /// The Python engine parses a source only when a rule needs its syntax, so
    /// a syntax error stops a check only then.
    pub fn read_tolerant(source: &SourceFile) -> Result<Self, String> {
        let bytes = vfs::read(&source.absolute)
            .map_err(|error| format!("cannot read source {}: {error}", source.path))?;
        let body = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes);
        let decoded = std::str::from_utf8(body)
            .map_err(|error| format!("cannot read source {}: {error}", source.path))?;
        // Python reads sources with universal newlines.
        let normalized = decoded.replace("\r\n", "\n").replace('\r', "\n");
        let parsed = parse_unchecked(&normalized, ParseOptions::from(Mode::Module))
            .try_into_module()
            .ok_or_else(|| format!("cannot parse source {} as a module", source.path))?;
        let syntax_error = parsed.errors().first().map(|error| {
            let offset = usize::from(error.location.start());
            let line = normalized[..offset].matches('\n').count() + 1;
            format!("{}:{line}: syntax error: {}", source.path, error.error)
        });
        Ok(Self {
            text: SourceText::new(normalized),
            parsed,
            syntax_error,
        })
    }

    pub fn module(&self) -> &ModModule {
        self.parsed.syntax()
    }

    /// Where CPython places a function: at `def`/`async`, after its decorators.
    pub fn function_start(&self, function: &StmtFunctionDef) -> TextSize {
        let Some(decorator) = function.decorator_list.last() else {
            return function.start();
        };
        self.parsed
            .tokens()
            .after(decorator.end())
            .iter()
            .find(|token| matches!(token.kind(), TokenKind::Def | TokenKind::Async))
            .map_or(function.start(), Ranged::start)
    }

    /// Where CPython places a class: at `class`, after its decorators.
    pub fn class_start(&self, class: &StmtClassDef) -> TextSize {
        let Some(decorator) = class.decorator_list.last() else {
            return class.start();
        };
        self.parsed
            .tokens()
            .after(decorator.end())
            .iter()
            .find(|token| token.kind() == TokenKind::Class)
            .map_or(class.start(), Ranged::start)
    }

    /// Where CPython places a statement, which skips the decorators of a definition.
    pub fn statement_start(&self, statement: &Stmt) -> TextSize {
        match statement {
            Stmt::FunctionDef(node) => self.function_start(node),
            Stmt::ClassDef(node) => self.class_start(node),
            other => other.start(),
        }
    }

    /// CPython's span of a comprehension: a bare generator argument owns the
    /// parentheses of its call.
    pub fn comprehension_range(&self, expression: &Expr) -> TextRange {
        let range = expression.range();
        let Expr::Generator(generator) = expression else {
            return range;
        };
        if generator.parenthesized {
            return range;
        }
        let tokens = self.parsed.tokens();
        let open = tokens
            .before(range.start())
            .iter()
            .rev()
            .find(|token| !token.kind().is_trivia());
        let close = tokens
            .after(range.end())
            .iter()
            .find(|token| !token.kind().is_trivia());
        match (open, close) {
            (Some(open), Some(close))
                if open.kind() == TokenKind::Lpar && close.kind() == TokenKind::Rpar =>
            {
                TextRange::new(open.start(), close.end())
            }
            _ => range,
        }
    }
}
