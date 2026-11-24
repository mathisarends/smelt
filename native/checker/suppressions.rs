//! `# smelt: ignore` comments with the statement spans of the Python tokenizer.
use super::source::ParsedSource;
use super::violation::Violation;
use regex::Regex;
use ruff_python_ast::token::TokenKind;
use ruff_text_size::Ranged;
use std::collections::HashMap;
use std::sync::LazyLock;

static DIRECTIVE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"#\s*smelt:\s*(?P<kind>ignore-file|ignore)\b(?:\[(?P<codes>[^\]]*)\])?(?:\s*--\s*(?P<reason>.*))?",
    )
    .expect("the directive pattern is valid")
});

pub struct Suppression {
    path_line: usize,
    file_level: bool,
    codes: Vec<String>,
    span: Option<(usize, usize)>,
}

impl Suppression {
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
                || violation.line == Some(self.path_line)
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
        let codes = captures
            .name("codes")
            .map_or("", |codes| codes.as_str())
            .split(',')
            .filter(|code| !code.trim().is_empty())
            .map(identifier)
            .collect();
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
        found.push(Suppression {
            path_line: line,
            file_level: &captures["kind"] == "ignore-file",
            codes,
            span,
        });
    }
    found
}

fn is_rule_name(identifier: &str) -> bool {
    identifier.contains('/') || identifier.contains('-')
}

fn identifier(raw: &str) -> String {
    let value = raw.trim();
    if is_rule_name(value) {
        value.to_owned()
    } else {
        value.to_uppercase()
    }
}
