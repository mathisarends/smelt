//! One parsed source with CPython-compatible positions.
use super::files::SourceFile;
use ruff_python_ast::token::TokenKind;
use ruff_python_ast::{ModModule, StmtFunctionDef};
use ruff_python_parser::{Parsed, parse_module};
use ruff_text_size::{Ranged, TextSize};
use serde_json::{Value, json};
use std::fs;

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
}

impl ParsedSource {
    pub fn read(source: &SourceFile) -> Result<Self, String> {
        let bytes = fs::read(&source.absolute)
            .map_err(|error| format!("cannot read source {}: {error}", source.path))?;
        let body = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes);
        let decoded = std::str::from_utf8(body)
            .map_err(|error| format!("cannot read source {}: {error}", source.path))?;
        // Python reads sources with universal newlines.
        let normalized = decoded.replace("\r\n", "\n").replace('\r', "\n");
        let parsed = parse_module(&normalized).map_err(|error| {
            let offset = usize::from(error.location.start());
            let line = normalized[..offset].matches('\n').count() + 1;
            format!("{}:{line}: syntax error: {}", source.path, error.error)
        })?;
        Ok(Self {
            text: SourceText::new(normalized),
            parsed,
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
}
