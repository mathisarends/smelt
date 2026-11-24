//! `# smelt: ignore` comments with the statement spans of the Python tokenizer.
use super::source::{ParsedSource, SourceText};
use super::violation::Violation;
use regex::Regex;
use ruff_python_ast::token::TokenKind;
use ruff_text_size::Ranged;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::LazyLock;

static DIRECTIVE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"#\s*smelt:\s*(?P<kind>ignore-file|ignore)\b(?:\[(?P<codes>[^\]]*)\])?(?:\s*--\s*(?P<reason>.*))?",
    )
    .expect("the directive pattern is valid")
});

pub struct Suppression {
    pub line: usize,
    /// The 0-based character column of the directive, as `tokenize` counts.
    pub column: usize,
    /// The byte offset of the directive within its line.
    byte_column: usize,
    file_level: bool,
    /// Codes, code prefixes and unresolved names; empty for every code.
    pub codes: Vec<String>,
    /// The identifiers as the comment spells them, parallel to `codes`.
    written: Vec<String>,
    pub reason: Option<String>,
    span: Option<(usize, usize)>,
}

impl Suppression {
    /// The identifier as the comment spells it.
    pub fn label<'s>(&'s self, code: &'s str) -> &'s str {
        self.codes
            .iter()
            .zip(&self.written)
            .rev()
            .find(|(resolved, _)| *resolved == code)
            .map_or(code, |(_, written)| written.as_str())
    }

    /// The listed code prefixes that match none of the `used` codes.
    pub fn unused_codes<'s>(&'s self, used: &[String]) -> Vec<&'s str> {
        self.codes
            .iter()
            .filter(|prefix| !used.iter().any(|code| code.starts_with(prefix.as_str())))
            .map(String::as_str)
            .collect()
    }

    /// Replace rule names with their codes; unknown names stay as written.
    pub fn resolve_names(mut self, names: &HashMap<&str, &str>) -> Self {
        for code in &mut self.codes {
            if is_rule_name(code)
                && let Some(resolved) = names.get(code.as_str())
            {
                *code = (*resolved).to_owned();
            }
        }
        self
    }

    pub fn matches(&self, violation: &Violation) -> bool {
        let covers = self.codes.is_empty()
            || self
                .codes
                .iter()
                .any(|prefix| violation.code.starts_with(prefix.as_str()));
        covers
            && (self.file_level
                || violation.line == Some(self.line)
                || self
                    .span
                    .zip(violation.line)
                    .is_some_and(|((first, last), line)| (first..=last).contains(&line)))
    }
}

pub fn parse(source: &ParsedSource) -> Vec<Suppression> {
    let tokens = source.parsed.tokens();
    let mut statements: Vec<(usize, usize, bool)> = Vec::new();
    let mut start: Option<(usize, bool)> = None;
    for token in tokens {
        let kind = token.kind();
        if kind == TokenKind::Newline {
            if let Some((first, is_import)) = start.take() {
                let (last, _) = source.text.position(usize::from(token.start()));
                statements.push((first, last, is_import));
            }
        } else if !matches!(
            kind,
            TokenKind::Comment
                | TokenKind::NonLogicalNewline
                | TokenKind::Indent
                | TokenKind::Dedent
                | TokenKind::EndOfFile
        ) && start.is_none()
        {
            let (line, _) = source.text.position(usize::from(token.start()));
            start = Some((line, matches!(kind, TokenKind::From | TokenKind::Import)));
        }
    }
    let mut found = Vec::new();
    for token in tokens {
        if token.kind() != TokenKind::Comment {
            continue;
        }
        let offset = usize::from(token.start());
        let comment = &source.text.text[offset..usize::from(token.end())];
        let Some(captures) = DIRECTIVE.captures(comment) else {
            continue;
        };
        let codes: Vec<String> = captures
            .name("codes")
            .map_or("", |codes| codes.as_str())
            .split(',')
            .filter(|code| !code.trim().is_empty())
            .map(identifier)
            .collect();
        let reason = captures
            .name("reason")
            .map(|reason| reason.as_str().trim())
            .filter(|reason| !reason.is_empty())
            .map(str::to_owned);
        let (line, column) = source.text.position(offset);
        let before = &source.text.text[offset - column..offset];
        // Inline exceptions follow their statement; interior import comments stay local.
        let span = statements
            .iter()
            .find(|(first, last, is_import)| {
                *first <= line
                    && line <= *last
                    && (!is_import
                        || line == *first
                        || (line == *last && before.trim_start().starts_with([')', ']', '}'])))
            })
            .map(|(first, last, _)| (*first, *last));
        let byte_column = column + captures.get(0).map_or(0, |found| found.start());
        let line_start = offset - column;
        found.push(Suppression {
            line,
            column: source.text.text[line_start..line_start + byte_column]
                .chars()
                .count(),
            byte_column,
            file_level: &captures["kind"] == "ignore-file",
            written: codes.clone(),
            codes,
            reason,
            span,
        });
    }
    found
}

/// Rewrite the directive's identifiers through `drop`, or remove it entirely.
///
/// `drop` maps identifiers to their replacements, none removing them; `None`, or
/// nothing left, removes the directive and the whitespace before it, or its whole
/// line when nothing else is on it.
pub fn directive_edit(
    text: &SourceText,
    suppression: &Suppression,
    drop: Option<&[(String, Vec<String>)]>,
) -> Option<Value> {
    let start = text.line_start(suppression.line);
    let end = text.line_end(suppression.line);
    let line = text.text[start..end].trim_end_matches(['\r', '\n']);
    let column = suppression.byte_column;
    let captures = DIRECTIVE_AT.captures(line.get(column..)?)?;
    if let (Some(drop), Some(codes)) = (drop, captures.name("codes")) {
        let kept: Vec<String> = codes
            .as_str()
            .split(',')
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .flat_map(|item| {
                let key = identifier(item);
                drop.iter()
                    .find(|(dropped, _)| *dropped == key)
                    .map_or_else(
                        || vec![item.to_owned()],
                        |(_, replacements)| replacements.clone(),
                    )
            })
            .collect();
        if !kept.is_empty() {
            return Some(text.edit(
                start + column + codes.start(),
                start + column + codes.end(),
                &kept.join(", "),
            ));
        }
    }
    let matched = captures.get(0)?;
    let directive_end = column + matched.end();
    let cut = line[..column].trim_end_matches([' ', '\t']).len();
    if line[..cut].trim().is_empty() && line[directive_end..].trim().is_empty() {
        return Some(text.edit(start, end, ""));
    }
    Some(text.edit(start + cut, start + directive_end, ""))
}

/// The directive at the start of a string, as `re.match` at a column finds it.
static DIRECTIVE_AT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!("^(?:{})", DIRECTIVE.as_str())).expect("the directive pattern is valid")
});

pub fn is_rule_name(identifier: &str) -> bool {
    identifier.contains('/') || identifier.contains('-')
}

/// Rule names keep their spelling; codes and code prefixes ignore case.
pub fn identifier(raw: &str) -> String {
    let value = raw.trim();
    if is_rule_name(value) {
        value.to_owned()
    } else {
        value.to_uppercase()
    }
}
