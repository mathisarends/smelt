//! Findings as `smelt.diagnostics.violation.Violation` holds and writes them.
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::cmp::Ordering;
use std::fmt::Write;

pub const DOCS_BASE_URL: &str = "https://github.com/mathisarends/smelt/blob/main/docs/rules";

/// `docs_url`: the rule's documentation page.
pub fn docs_url(code: &str) -> String {
    format!("{DOCS_BASE_URL}/{code}.md")
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Debug)]
pub enum Severity {
    Hint,
    Warning,
    Error,
}

impl Severity {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "error" => Some(Self::Error),
            "warning" => Some(Self::Warning),
            "hint" => Some(Self::Hint),
            _ => None,
        }
    }

    pub fn value(self) -> &'static str {
        match self {
            Self::Error => "error",
            Self::Warning => "warning",
            Self::Hint => "hint",
        }
    }

    pub fn at_least(self, other: Self) -> bool {
        self >= other
    }
}

#[derive(Clone, PartialEq, Debug)]
pub struct ImportLink {
    pub importer: String,
    pub imported: String,
    pub line: Option<u64>,
}

impl ImportLink {
    pub fn to_json(&self) -> Value {
        json!({"importer": self.importer, "imported": self.imported, "line": self.line})
    }
}

#[derive(Clone, Debug)]
pub struct Finding {
    pub code: String,
    pub rule: String,
    pub severity: Severity,
    pub message: String,
    pub path: Option<String>,
    pub line: Option<u64>,
    pub column: Option<u64>,
    pub end_line: Option<u64>,
    pub end_column: Option<u64>,
    pub source_module: Option<String>,
    pub target_module: Option<String>,
    pub import_chain: Vec<ImportLink>,
    pub feature: Option<String>,
    pub layer: Option<String>,
    pub expected: Option<Map<String, Value>>,
    pub hint: Option<String>,
    pub edge: Option<String>,
    pub docs_url: Option<String>,
    pub category: Option<String>,
    pub member: Option<String>,
    pub fix: Option<Value>,
}

fn text(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}

fn number(value: &Value, key: &str) -> Option<u64> {
    value.get(key).and_then(Value::as_u64)
}

impl Finding {
    /// A finding the core reported; `None` for a shape the Python model would reject.
    pub fn from_core(value: &Value) -> Option<Self> {
        let chain = match value.get("import_chain") {
            None | Some(Value::Null) => Vec::new(),
            Some(Value::Array(links)) => links
                .iter()
                .map(|link| {
                    Some(ImportLink {
                        importer: text(link, "importer")?,
                        imported: text(link, "imported")?,
                        line: number(link, "line"),
                    })
                })
                .collect::<Option<_>>()?,
            Some(_) => return None,
        };
        let expected = match value.get("expected") {
            None | Some(Value::Null) => None,
            Some(Value::Object(entries)) => Some(entries.clone()),
            Some(_) => return None,
        };
        let fix = match value.get("fix") {
            None | Some(Value::Null) => None,
            Some(fix) => Some(canonical_fix(fix)?),
        };
        Some(Self {
            code: text(value, "code")?,
            rule: text(value, "rule")?,
            severity: Severity::parse(value.get("severity")?.as_str()?)?,
            message: text(value, "message")?,
            path: text(value, "path"),
            line: number(value, "line"),
            column: number(value, "column"),
            end_line: number(value, "end_line"),
            end_column: number(value, "end_column"),
            source_module: text(value, "source_module"),
            target_module: text(value, "target_module"),
            import_chain: chain,
            feature: text(value, "feature"),
            layer: text(value, "layer"),
            expected,
            hint: text(value, "hint"),
            edge: text(value, "edge"),
            docs_url: text(value, "docs_url"),
            category: text(value, "category"),
            member: text(value, "member"),
            fix,
        })
    }

    pub fn compare(left: &Self, right: &Self) -> Ordering {
        (
            left.path.as_deref().unwrap_or(""),
            left.line.unwrap_or(0),
            left.column.unwrap_or(0),
            &left.code,
            &left.message,
        )
            .cmp(&(
                right.path.as_deref().unwrap_or(""),
                right.line.unwrap_or(0),
                right.column.unwrap_or(0),
                &right.code,
                &right.message,
            ))
    }

    /// A short id, stable for the same finding at the same place.
    pub fn finding_id(&self) -> String {
        let payload = format!(
            "{}\x1f{}\x1f{}\x1f{}\x1f{}",
            self.code,
            self.path.as_deref().unwrap_or(""),
            self.line.unwrap_or(0),
            self.column.unwrap_or(0),
            self.message
        );
        let digest = Sha256::digest(payload.as_bytes());
        let mut id = String::with_capacity(12);
        for byte in &digest[..6] {
            let _ = write!(id, "{byte:02x}");
        }
        id
    }

    pub fn expected_get(&self, key: &str) -> Option<&Value> {
        self.expected.as_ref().and_then(|entries| entries.get(key))
    }

    /// The fix's applicability, as `Applicability` reads it.
    pub fn applicability(&self) -> Option<&str> {
        self.fix
            .as_ref()
            .and_then(|fix| fix.get("applicability"))
            .and_then(Value::as_str)
    }

    pub fn to_json(&self) -> Value {
        let mut data = Map::new();
        data.insert("id".into(), json!(self.finding_id()));
        data.insert("code".into(), json!(self.code));
        data.insert("rule".into(), json!(self.rule));
        data.insert("severity".into(), json!(self.severity.value()));
        data.insert("category".into(), json!(self.category));
        data.insert("message".into(), json!(self.message));
        data.insert("path".into(), json!(self.path));
        data.insert("line".into(), json!(self.line));
        data.insert("column".into(), json!(self.column));
        data.insert("end_line".into(), json!(self.end_line));
        data.insert("end_column".into(), json!(self.end_column));
        data.insert("feature".into(), json!(self.feature));
        data.insert("layer".into(), json!(self.layer));
        data.insert("member".into(), json!(self.member));
        data.insert("source_module".into(), json!(self.source_module));
        data.insert("target_module".into(), json!(self.target_module));
        data.insert("edge".into(), json!(self.edge));
        if !self.import_chain.is_empty() {
            data.insert(
                "import_chain".into(),
                Value::Array(self.import_chain.iter().map(ImportLink::to_json).collect()),
            );
        }
        data.insert(
            "expected".into(),
            self.expected.clone().map_or(Value::Null, Value::Object),
        );
        data.insert("hint".into(), json!(self.hint));
        data.insert("fix".into(), self.fix.clone().unwrap_or(Value::Null));
        data.insert("docs_url".into(), json!(self.docs_url));
        Value::Object(data)
    }
}

/// A fix in the shape `Fix.to_json` writes; `None` for one `Fix` would reject.
fn canonical_fix(fix: &Value) -> Option<Value> {
    let applicability = fix.get("applicability")?.as_str()?;
    if !matches!(applicability, "safe" | "unsafe" | "display") {
        return None;
    }
    let edits = fix
        .get("edits")?
        .as_array()?
        .iter()
        .map(|edit| {
            Some(json!({
                "line": edit.get("line")?.as_u64()?,
                "column": edit.get("column")?.as_u64()?,
                "end_line": edit.get("end_line")?.as_u64()?,
                "end_column": edit.get("end_column")?.as_u64()?,
                "original": edit.get("original")?.as_str()?,
                "replacement": edit.get("replacement")?.as_str()?,
            }))
        })
        .collect::<Option<Vec<_>>>()?;
    Some(json!({
        "applicability": applicability,
        "description": fix.get("description")?.as_str()?,
        "edits": edits,
    }))
}
