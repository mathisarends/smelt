//! JSON text as Python's `json.dumps` writes it, byte for byte.
use serde_json::{Number, Value};
use std::fmt::Write;

/// `json.dumps(value, indent=2, ensure_ascii=False)`.
pub fn indented(value: &Value) -> String {
    let mut out = String::new();
    write_indented(&mut out, value, 0);
    out
}

/// `json.dumps(value, sort_keys=True)`: ASCII only, `", "` and `": "` separators.
pub fn sorted_ascii(value: &Value) -> String {
    let mut out = String::new();
    write_inline(&mut out, value, true, (", ", ": "), true);
    out
}

/// `json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
pub fn sorted_compact(value: &Value) -> String {
    let mut out = String::new();
    write_inline(&mut out, value, true, (",", ":"), false);
    out
}

fn write_indented(out: &mut String, value: &Value, depth: usize) {
    match value {
        Value::Array(items) if !items.is_empty() => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                newline(out, depth + 1);
                write_indented(out, item, depth + 1);
            }
            newline(out, depth);
            out.push(']');
        }
        Value::Object(entries) if !entries.is_empty() => {
            out.push('{');
            for (index, (key, item)) in entries.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                newline(out, depth + 1);
                string(out, key, false);
                out.push_str(": ");
                write_indented(out, item, depth + 1);
            }
            newline(out, depth);
            out.push('}');
        }
        other => scalar(out, other, false),
    }
}

fn newline(out: &mut String, depth: usize) {
    out.push('\n');
    for _ in 0..depth {
        out.push_str("  ");
    }
}

fn write_inline(
    out: &mut String,
    value: &Value,
    sort: bool,
    separators: (&str, &str),
    ascii: bool,
) {
    match value {
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(separators.0);
                }
                write_inline(out, item, sort, separators, ascii);
            }
            out.push(']');
        }
        Value::Object(entries) => {
            let mut pairs: Vec<(&String, &Value)> = entries.iter().collect();
            if sort {
                pairs.sort_by(|left, right| left.0.cmp(right.0));
            }
            out.push('{');
            for (index, (key, item)) in pairs.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(separators.0);
                }
                string(out, key, ascii);
                out.push_str(separators.1);
                write_inline(out, item, sort, separators, ascii);
            }
            out.push('}');
        }
        other => scalar(out, other, ascii),
    }
}

fn scalar(out: &mut String, value: &Value, ascii: bool) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(number) => out.push_str(&number_text(number)),
        Value::String(text) => string(out, text, ascii),
        Value::Array(_) => out.push_str("[]"),
        Value::Object(_) => out.push_str("{}"),
    }
}

/// A number as Python prints it: integers exactly, floats like `repr(float)`.
pub fn number_text(number: &Number) -> String {
    if number.is_i64() || number.is_u64() {
        return number.to_string();
    }
    float_repr(number.as_f64().unwrap_or(0.0))
}

/// `repr(value)` for a float: the shortest round-trip digits, in scientific
/// notation below 1e-4 or from 1e16.
pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".to_owned();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity" } else { "-Infinity" }.to_owned();
    }
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .to_owned();
    }
    // `{:e}` gives the shortest digits that round-trip, as `d.ddde<exp>`.
    let scientific = format!("{:e}", value.abs());
    let (mantissa, exponent) = scientific
        .split_once('e')
        .unwrap_or((scientific.as_str(), "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let sign = if value < 0.0 { "-" } else { "" };
    if (-4..16).contains(&exponent) {
        let point = exponent + 1;
        let text = if point <= 0 {
            format!("0.{}{digits}", "0".repeat(point.unsigned_abs() as usize))
        } else if point as usize >= digits.len() {
            format!("{digits}{}.0", "0".repeat(point as usize - digits.len()))
        } else {
            let (whole, fraction) = digits.split_at(point as usize);
            format!("{whole}.{fraction}")
        };
        return format!("{sign}{text}");
    }
    let (first, rest) = digits.split_at(1);
    let mantissa = if rest.is_empty() {
        first.to_owned()
    } else {
        format!("{first}.{rest}")
    };
    let exponent_sign = if exponent < 0 { '-' } else { '+' };
    format!("{sign}{mantissa}e{exponent_sign}{:02}", exponent.abs())
}

fn string(out: &mut String, text: &str, ascii: bool) {
    out.push('"');
    for character in text.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            control if (control as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", control as u32);
            }
            other if ascii && !other.is_ascii() => {
                let mut units = [0u16; 2];
                for unit in other.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
            other => out.push(other),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::{float_repr, indented, sorted_ascii, sorted_compact};
    use serde_json::json;

    #[test]
    fn floats_read_like_python_reprs() {
        let cases = [
            (0.1, "0.1"),
            (1.5, "1.5"),
            (100.0, "100.0"),
            (1e-5, "1e-05"),
            (0.0001, "0.0001"),
            (1e16, "1e+16"),
            (123_456_789.125, "123456789.125"),
            (-2.5e-7, "-2.5e-07"),
            (1e22, "1e+22"),
        ];
        for (value, text) in cases {
            assert_eq!(float_repr(value), text, "{value}");
        }
    }

    #[test]
    fn documents_read_like_python_dumps() {
        let document = json!({"b": [1, {"x": []}], "a": {}, "é": "\u{1}\"\n"});

        assert_eq!(
            indented(&document),
            "{\n  \"b\": [\n    1,\n    {\n      \"x\": []\n    }\n  ],\n  \"a\": {},\n  \"é\": \"\\u0001\\\"\\n\"\n}"
        );
        assert_eq!(
            sorted_ascii(&document),
            "{\"a\": {}, \"b\": [1, {\"x\": []}], \"\\u00e9\": \"\\u0001\\\"\\n\"}"
        );
        assert_eq!(
            sorted_compact(&document),
            "{\"a\":{},\"b\":[1,{\"x\":[]}],\"é\":\"\\u0001\\\"\\n\"}"
        );
    }
}
