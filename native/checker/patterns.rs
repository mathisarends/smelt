//! Path and module globs with the semantics of `smelt.config.patterns`.
use regex::Regex;

pub struct PathPattern(Regex);

impl PathPattern {
    pub fn new(pattern: &str) -> Result<Self, String> {
        let chars: Vec<char> = pattern.trim_matches('/').chars().collect();
        let mut expression = String::from("^(?:");
        let mut index = 0;
        while index < chars.len() {
            if chars[index..].starts_with(&['*', '*', '/']) {
                expression.push_str("(?:.*/)?");
                index += 3;
            } else if chars[index..].starts_with(&['*', '*']) {
                expression.push_str(".*");
                index += 2;
            } else {
                match chars[index] {
                    '*' => expression.push_str("[^/]*"),
                    '?' => expression.push_str("[^/]"),
                    other => expression.push_str(&regex::escape(&other.to_string())),
                }
                index += 1;
            }
        }
        expression.push_str(")(?:/.*)?$");
        Regex::new(&expression)
            .map(Self)
            .map_err(|error| error.to_string())
    }

    pub fn matches(&self, path: &str) -> bool {
        self.0.is_match(path)
    }
}

pub struct ModulePattern(Regex);

impl ModulePattern {
    pub fn new(pattern: &str) -> Result<Self, String> {
        let mut expression = String::from("^(?:");
        for (index, segment) in pattern.split('.').enumerate() {
            if segment == "**" {
                expression.push_str(if index == 0 {
                    r"[^.]+(?:\.[^.]+)*"
                } else {
                    r"(?:\.[^.]+)*"
                });
                continue;
            }
            if index > 0 {
                expression.push_str(r"\.");
            }
            expression.push_str(&regex::escape(segment).replace(r"\*", "[^.]*"));
        }
        expression.push_str(")$");
        Regex::new(&expression)
            .map(Self)
            .map_err(|error| error.to_string())
    }

    pub fn matches(&self, module: &str) -> bool {
        self.0.is_match(module)
    }
}

/// Whether `module` is `package` or lies below it.
pub fn is_within(module: &str, package: &str) -> bool {
    module
        .strip_prefix(package)
        .is_some_and(|rest| rest.is_empty() || rest.starts_with('.'))
}
