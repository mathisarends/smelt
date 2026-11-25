//! Comment tokens by line, the tool directives they carry and whether they explain themselves.
use crate::checker::source::ParsedSource;
use regex::Regex;
use ruff_python_ast::token::TokenKind;
use ruff_text_size::Ranged;
use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

const SEPARATORS: [char; 7] = [' ', '\t', '-', ':', ';', ',', '.'];

/// Tool directives. The classes spell out what Python's `re` matches and Rust's does not:
/// `\s` includes U+001C to U+001F, `\w` is `str.isalnum` plus `_`, and `re.IGNORECASE` folds
/// `i` with U+0130 and U+0131.
static DIRECTIVE: LazyLock<Regex> = LazyLock::new(|| {
    let pattern = r"(?ix)\A S*(?:
        (?: (?: ruff | flake8 ): S* )? noqa(?: : S* C+[0-9]+ (?: [\s\x1c-\x1f,]+ C+[0-9]+ )* )?
      | pylInt: S* dIsable (?: -next )? = W+ (?: S* , S* W+ )*
      | (?: type | pyrIght ): S* Ignore (?: \[[^\]]*\] )?
      | mypy: S* (?: Ignore-errors | dIsable-error-code S* = S* W+ (?: S* , S* W+ )* )
      | pragma: S* no S+ (?: cover | branch )
      | smelt: S* Ignore (?: -fIle )? (?: \[[^\]]*\] )?
      | fmt: S* (?: on | off | skIp )
    )";
    Regex::new(&expand(pattern)).expect("the directive pattern is valid")
});

/// The directives that silence another tool's findings, by the label a finding names.
static SUPPRESSION: LazyLock<Regex> = LazyLock::new(|| {
    let pattern = r"(?ix)\A S*(?:
        (?P<tool> ruff | flake8 ): S* noqa\b
      | (?P<noqa> noqa )\b
      | (?P<pylint> pylInt: S* dIsable (?: -next )? ) S* =
      | (?P<checker> type | pyrIght ): S* Ignore\b
      | (?P<mypy> mypy: S* (?: Ignore-errors | dIsable-error-code ) )\b
    )";
    Regex::new(&expand(pattern)).expect("the suppression pattern is valid")
});

/// `str.isalnum`.
static ALPHANUMERIC: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[\p{L}\p{N}]").expect("the pattern is valid"));

fn expand(pattern: &str) -> String {
    pattern
        .replace("S*", r"[\s\x1c-\x1f]*")
        .replace("S+", r"[\s\x1c-\x1f]+")
        .replace("C+", r"[A-Z\x{130}\x{131}]+")
        .replace("W+", r"[\p{L}\p{N}_-]+")
        .replace('I', r"[i\x{130}\x{131}]")
}

/// Whether a comment says something besides its tool directives.
pub fn is_reason(comment: &str) -> bool {
    comment.split('#').any(|segment| {
        let mut rest = segment;
        while let Some(directive) = DIRECTIVE.find(rest) {
            rest = rest[directive.end()..].trim_start_matches(SEPARATORS);
        }
        ALPHANUMERIC.is_match(rest)
    })
}

/// The directives in a comment that silence another tool, such as `type: ignore`.
pub fn tool_suppressions(comment: &str) -> Vec<String> {
    comment
        .split('#')
        .filter_map(|segment| {
            let captures = SUPPRESSION.captures(segment)?;
            let label = if let Some(tool) = captures.name("tool") {
                format!("{}: noqa", tool.as_str().to_lowercase())
            } else if captures.name("noqa").is_some() {
                "noqa".to_owned()
            } else if let Some(checker) = captures.name("checker") {
                format!("{}: ignore", checker.as_str().to_lowercase())
            } else {
                let directive = captures.name("pylint").or_else(|| captures.name("mypy"))?;
                directive
                    .as_str()
                    .split(':')
                    .map(str::trim)
                    .collect::<Vec<_>>()
                    .join(": ")
                    .to_lowercase()
            };
            Some(label)
        })
        .collect()
}

/// The comments of a module by line, and which of them stand on a line of their own.
pub struct Comments<'a> {
    texts: HashMap<usize, (&'a str, usize)>,
    standalone: HashSet<usize>,
}

impl<'a> Comments<'a> {
    pub fn scan(parsed: &'a ParsedSource) -> Self {
        let text = parsed.text.text.as_str();
        let mut texts = HashMap::new();
        let mut standalone = HashSet::new();
        for token in parsed.parsed.tokens() {
            if token.kind() != TokenKind::Comment {
                continue;
            }
            let start = usize::from(token.start());
            let (line, column) = parsed.text.position(start);
            texts.insert(line, (&text[start..usize::from(token.end())], start));
            if text[start - column..start].trim().is_empty() {
                standalone.insert(line);
            }
        }
        Self { texts, standalone }
    }

    /// The comments in line order, with their 1-based line and byte offset.
    pub fn ordered(&self) -> Vec<(usize, usize, &'a str)> {
        let mut found: Vec<(usize, usize, &str)> = self
            .texts
            .iter()
            .map(|(line, (text, offset))| (*line, *offset, *text))
            .collect();
        found.sort_unstable();
        found
    }

    /// The comment on a line.
    pub fn text(&self, line: usize) -> Option<&'a str> {
        self.texts.get(&line).map(|(text, _)| *text)
    }

    /// The byte offset of the comment on a line.
    pub fn offset(&self, line: usize) -> Option<usize> {
        self.texts.get(&line).map(|(_, offset)| *offset)
    }

    /// The lines of the comment block that ends right above `line`, nearest first.
    pub fn block_above(&self, line: usize) -> Vec<usize> {
        let mut found = Vec::new();
        let mut above = line.saturating_sub(1);
        while above > 0 && self.standalone.contains(&above) {
            found.push(above);
            above -= 1;
        }
        found
    }
}
