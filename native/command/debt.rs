//! Debt files as `smelt.diagnostics.debt.Debt` reads and matches them.
use super::finding::Finding;
use super::pyjson::truthy;
use super::pytext::{display, squash};
use crate::checker::renames::{current_code, legacy_code};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fmt::Write;

/// One accepted finding, by its fingerprint.
pub struct Entry {
    pub fingerprint: String,
    pub code: String,
    pub path: Option<String>,
    pub message: String,
}

/// The entries of the debt file at `text`; `Err` where the strict Python
/// models would reject the document.
pub fn parse(text: &str) -> Result<Vec<Entry>, ()> {
    let document: Value = serde_json::from_str(text).map_err(|_| ())?;
    let document = document.as_object().ok_or(())?;
    let version = document.get("version").and_then(Value::as_u64).ok_or(())?;
    if document
        .keys()
        .any(|key| key != "version" && key != "violations")
    {
        return Err(());
    }
    let items = document
        .get("violations")
        .and_then(Value::as_array)
        .ok_or(())?;
    let mut entries = Vec::new();
    for item in items {
        let item = item.as_object().ok_or(())?;
        let code = current_code(required_text(item, "code")?).to_owned();
        let path = optional_text(item, "path")?;
        let message = match item.get("message") {
            None => String::new(),
            Some(Value::String(message)) => message.clone(),
            Some(_) => return Err(()),
        };
        match version {
            1 => {
                only(item, &["fingerprint", "code", "path", "message"])?;
                entries.push(Entry {
                    fingerprint: required_text(item, "fingerprint")?.to_owned(),
                    code,
                    path,
                    message,
                });
            }
            2 => {
                only(item, &["code", "path", "message", "scope", "fingerprints"])?;
                optional_text(item, "scope")?;
                let fingerprints = item
                    .get("fingerprints")
                    .and_then(Value::as_array)
                    .filter(|keys| !keys.is_empty())
                    .ok_or(())?;
                for key in fingerprints {
                    entries.push(Entry {
                        fingerprint: key.as_str().ok_or(())?.to_owned(),
                        code: code.clone(),
                        path: path.clone(),
                        message: message.clone(),
                    });
                }
            }
            _ => return Err(()),
        }
    }
    Ok(entries)
}

fn only(item: &Map<String, Value>, keys: &[&str]) -> Result<(), ()> {
    if item.keys().all(|key| keys.contains(&key.as_str())) {
        Ok(())
    } else {
        Err(())
    }
}

fn required_text<'a>(item: &'a Map<String, Value>, key: &str) -> Result<&'a str, ()> {
    item.get(key).and_then(Value::as_str).ok_or(())
}

fn optional_text(item: &Map<String, Value>, key: &str) -> Result<Option<String>, ()> {
    match item.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(text)) => Ok(Some(text.clone())),
        Some(_) => Err(()),
    }
}

/// `Debt.from_violations`: findings as debt, each fingerprinted by the text
/// of its line.
pub fn from_findings(findings: &[(Finding, String)]) -> Vec<Entry> {
    findings
        .iter()
        .flat_map(|(finding, snippet)| {
            let edges = semantic_edges(finding);
            let targeted = finding
                .target_module
                .as_deref()
                .is_some_and(|module| !module.is_empty());
            let text = if targeted || !edges.is_empty() {
                ""
            } else {
                snippet.as_str()
            };
            fingerprints(finding, &edges, text)
                .into_iter()
                .map(|fingerprint| Entry {
                    fingerprint,
                    code: finding.code.clone(),
                    path: finding.path.clone(),
                    message: finding.message.clone(),
                })
        })
        .collect()
}

/// The findings no entry accepts, how many entries accepted, and the
/// entries nothing matched.
pub struct Matched {
    pub new: Vec<Finding>,
    pub known: usize,
    pub resolved: Vec<Entry>,
}

/// `Debt.match`; `snippet` is the text of a finding's line, `Err` for a file
/// the Python engine could not read.
pub fn match_findings(
    entries: Vec<Entry>,
    findings: Vec<Finding>,
    snippet: &mut dyn FnMut(&Finding) -> Result<String, ()>,
) -> Result<Matched, ()> {
    let mut available: HashMap<String, usize> = HashMap::new();
    for entry in &entries {
        *available.entry(entry.fingerprint.clone()).or_default() += 1;
    }
    let mut take = |key: &str| match available.get_mut(key) {
        Some(count) if *count > 0 => {
            *count -= 1;
            true
        }
        _ => false,
    };
    let mut new = Vec::new();
    let mut known = 0;
    for finding in findings {
        let edges = semantic_edges(&finding);
        let targeted = finding
            .target_module
            .as_deref()
            .is_some_and(|module| !module.is_empty());
        let mut text = if targeted || !edges.is_empty() {
            String::new()
        } else {
            snippet(&finding)?
        };
        let keys = fingerprints(&finding, &edges, &text);
        let mut matched: HashSet<&str> = HashSet::new();
        for key in &keys {
            if take(key) {
                matched.insert(key);
            }
        }
        let scoped = finding.code == "TYP003" && scope(&finding).is_some();
        if matched.is_empty() && edges.is_empty() && !scoped {
            // Version-1 debt files hashed the complete source line.
            if targeted {
                text = snippet(&finding)?;
            }
            if take(&fingerprint(&finding, &text, true)) {
                matched.insert(&keys[0]);
            }
        }
        if !matched.is_empty() {
            known += 1;
        }
        if matched.len() < keys.len() {
            new.push(finding);
        }
    }
    let resolved = entries
        .into_iter()
        .filter(|entry| take(&entry.fingerprint))
        .collect();
    Ok(Matched {
        new,
        known,
        resolved,
    })
}

fn hashed(payload: &str) -> String {
    let digest = Sha256::digest(payload.as_bytes());
    let mut text = String::with_capacity(32);
    for byte in &digest[..16] {
        let _ = write!(text, "{byte:02x}");
    }
    text
}

/// `fingerprint`: an import finding hashes its edge, any other its line.
fn fingerprint(finding: &Finding, snippet: &str, legacy: bool) -> String {
    let targeted = finding
        .target_module
        .as_deref()
        .is_some_and(|module| !module.is_empty());
    let normalized = if targeted && !legacy {
        String::new()
    } else {
        squash(snippet)
    };
    let mut payload = [
        legacy_code(&finding.code),
        finding.path.as_deref().unwrap_or(""),
        finding.source_module.as_deref().unwrap_or(""),
        finding.target_module.as_deref().unwrap_or(""),
        &normalized,
    ]
    .join("\x1f");
    if finding.code == "TYP003"
        && !legacy
        && let Some(scope) = scope(finding)
    {
        payload.push('\x1f');
        payload.push_str(scope);
    }
    hashed(&payload)
}

/// `_scope`: the definition or scope a typing finding is about.
fn scope(finding: &Finding) -> Option<&str> {
    let definition = finding.expected_get("definition");
    let value = if definition.is_some_and(truthy) {
        definition
    } else {
        finding.expected_get("scope")
    };
    value
        .and_then(Value::as_str)
        .filter(|scope| !scope.is_empty())
}

fn fingerprints(finding: &Finding, edges: &[String], snippet: &str) -> Vec<String> {
    if edges.is_empty() {
        return vec![fingerprint(finding, snippet, false)];
    }
    let code = legacy_code(&finding.code);
    let prefix = if finding.code == "ARC004" {
        code.to_owned()
    } else {
        format!(
            "{code}\x1f{}\x1f{}",
            finding.path.as_deref().unwrap_or("None"),
            finding.source_module.as_deref().unwrap_or("None"),
        )
    };
    edges
        .iter()
        .map(|edge| hashed(&format!("{prefix}\x1f{edge}")))
        .collect()
}

/// `_semantic_edges`: the architecture edges one finding stands for.
fn semantic_edges(finding: &Finding) -> Vec<String> {
    let Some(field) = edge_field(&finding.code) else {
        return Vec::new();
    };
    match finding.expected.as_ref() {
        Some(expected) if !expected.is_empty() => match expected.get(field) {
            Some(Value::Array(edges)) => edges.iter().map(display).collect(),
            _ => Vec::new(),
        },
        _ => Vec::new(),
    }
}

fn edge_field(code: &str) -> Option<&'static str> {
    Some(match code {
        "ARC004" => "cyclic_edges",
        "ARC008" => "export_edges",
        "IMP002" => "import_edges",
        "LAY004" => "ordering_edges",
        "IDM002" => "dispatch_edges",
        "TYP005" => "literal_edges",
        "MOD012" => "pairing_edges",
        "IDM008" => "lookup_edges",
        "CPX006" => "comprehension_edges",
        "CPX007" => "decision_edges",
        "VIS007" => "definition_edges",
        "VIS011" => "reexport_edges",
        "TST005" => "patch_edges",
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::parse;

    #[test]
    fn debt_documents_parse_as_strictly_as_the_python_models() {
        let grouped = r#"{"version": 2, "violations": [{"code": "SMT101", "path": "a.py", "message": "m", "fingerprints": ["x", "y"]}]}"#;
        let entries = parse(grouped).expect("valid");

        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].code, "ARC001");
        assert!(
            parse(r#"{"version": 2, "violations": [{"code": "A", "fingerprints": []}]}"#).is_err()
        );
        assert!(parse(r#"{"version": 1.0, "violations": []}"#).is_err());
        assert!(parse(r#"{"version": 1, "violations": [], "extra": 1}"#).is_err());
        assert!(
            parse(r#"{"version": 1, "violations": [{"code": "A", "fingerprint": 1}]}"#).is_err()
        );
    }
}
