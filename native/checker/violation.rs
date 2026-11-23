//! Findings in the wire format of `Violation.to_json`.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::fmt::Write;

const DOCS_BASE_URL: &str = "https://github.com/mathisarends/smelt/blob/main/docs/rules";

pub struct Violation {
    pub code: String,
    pub rule: String,
    pub category: String,
    pub severity: String,
    pub message: String,
    pub path: String,
    pub line: usize,
    pub column: usize,
    pub end_line: Option<usize>,
    pub end_column: Option<usize>,
    pub source_module: Option<String>,
    pub expected: Value,
    pub hint: Option<String>,
    pub fix: Option<Value>,
}

impl Violation {
    pub fn compare(left: &Self, right: &Self) -> Ordering {
        (
            &left.path,
            left.line,
            left.column,
            &left.code,
            &left.message,
        )
            .cmp(&(
                &right.path,
                right.line,
                right.column,
                &right.code,
                &right.message,
            ))
    }

    pub fn to_json(&self) -> Value {
        json!({
            "id": self.finding_id(),
            "code": self.code,
            "rule": self.rule,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "feature": null,
            "layer": null,
            "member": null,
            "source_module": self.source_module,
            "target_module": null,
            "edge": null,
            "expected": self.expected,
            "hint": self.hint,
            "fix": self.fix,
            "docs_url": format!("{DOCS_BASE_URL}/{}.md", self.code),
        })
    }

    fn finding_id(&self) -> String {
        let payload = format!(
            "{}\x1f{}\x1f{}\x1f{}\x1f{}",
            self.code, self.path, self.line, self.column, self.message
        );
        let digest = Sha256::digest(payload.as_bytes());
        let mut id = String::with_capacity(12);
        for byte in &digest[..6] {
            write!(id, "{byte:02x}").expect("writing to a string cannot fail");
        }
        id
    }
}
