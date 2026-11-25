//! Python's string behavior where the report text depends on it.
use super::pyjson::number_text;
use serde_json::Value;

/// `str.isspace()` for one character.
pub fn is_space(character: char) -> bool {
    matches!(
        character,
        '\t' | '\n'
            | '\u{b}'
            | '\u{c}'
            | '\r'
            | '\u{1c}'..='\u{1f}'
            | ' '
            | '\u{85}'
            | '\u{a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

/// `" ".join(text.split())`.
pub fn squash(text: &str) -> String {
    text.split(is_space)
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// `text.strip() == ""`.
fn blank(text: &str) -> bool {
    text.chars().all(is_space)
}

/// The whitespace `textwrap` splits on and replaces with spaces.
fn wrap_space(character: char) -> bool {
    matches!(character, '\t' | '\n' | '\u{b}' | '\u{c}' | '\r' | ' ')
}

/// `textwrap.fill(text, width, initial_indent=..., subsequent_indent=...,
/// break_on_hyphens=False, break_long_words=False)`.
pub fn fill(text: &str, width: usize, initial: &str, subsequent: &str) -> String {
    let expanded = expand_tabs(text);
    let replaced: String = expanded
        .chars()
        .map(|character| {
            if wrap_space(character) {
                ' '
            } else {
                character
            }
        })
        .collect();
    let mut chunks: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut in_space = false;
    for character in replaced.chars() {
        let space = character == ' ';
        if !current.is_empty() && space != in_space {
            chunks.push(std::mem::take(&mut current));
        }
        in_space = space;
        current.push(character);
    }
    if !current.is_empty() {
        chunks.push(current);
    }
    chunks.reverse();
    let length = |text: &str| text.chars().count();
    let mut lines: Vec<String> = Vec::new();
    while !chunks.is_empty() {
        let mut line: Vec<String> = Vec::new();
        let mut used = 0;
        let indent = if lines.is_empty() {
            initial
        } else {
            subsequent
        };
        let room = width.saturating_sub(length(indent));
        if chunks.last().is_some_and(|chunk| blank(chunk)) && !lines.is_empty() {
            chunks.pop();
        }
        while let Some(chunk) = chunks.last() {
            let size = length(chunk);
            if used + size <= room {
                used += size;
                line.extend(chunks.pop());
            } else {
                break;
            }
        }
        if chunks.last().is_some_and(|chunk| length(chunk) > room) && line.is_empty() {
            line.extend(chunks.pop());
        }
        if line.last().is_some_and(|chunk| blank(chunk)) {
            line.pop();
        }
        if !line.is_empty() {
            lines.push(format!("{indent}{}", line.concat()));
        }
    }
    lines.join("\n")
}

/// `str.expandtabs()` with the default tab size of 8.
fn expand_tabs(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut column = 0;
    for character in text.chars() {
        match character {
            '\t' => {
                let spaces = 8 - column % 8;
                out.extend(std::iter::repeat_n(' ', spaces));
                column += spaces;
            }
            '\n' | '\r' => {
                out.push(character);
                column = 0;
            }
            other => {
                out.push(other);
                column += 1;
            }
        }
    }
    out
}

/// `str(value)` for a value decoded from JSON.
pub fn display(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => repr(other),
    }
}

/// `repr(value)` for a value decoded from JSON.
pub fn repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::Number(number) => {
            let text = number_text(number);
            match text.as_str() {
                "NaN" => "nan".to_owned(),
                "Infinity" => "inf".to_owned(),
                "-Infinity" => "-inf".to_owned(),
                _ => text,
            }
        }
        Value::String(text) => string_repr(text),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(entries) => format!(
            "{{{}}}",
            entries
                .iter()
                .map(|(key, item)| format!("{}: {}", string_repr(key), repr(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn string_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            other if other == quote => {
                out.push('\\');
                out.push(other);
            }
            other if printable(other) => out.push(other),
            other => {
                let code = other as u32;
                if code <= 0xff {
                    out.push_str(&format!("\\x{code:02x}"));
                } else if code <= 0xffff {
                    out.push_str(&format!("\\u{code:04x}"));
                } else {
                    out.push_str(&format!("\\U{code:08x}"));
                }
            }
        }
    }
    out.push(quote);
    out
}

/// `str.isprintable()` for the characters reports contain.
fn printable(character: char) -> bool {
    !matches!(
        character,
        '\u{0}'..='\u{1f}'
            | '\u{7f}'..='\u{a0}'
            | '\u{ad}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200f}'
            | '\u{2028}'..='\u{202f}'
            | '\u{205f}'..='\u{2064}'
            | '\u{3000}'
            | '\u{feff}'
            | '\u{e000}'..='\u{f8ff}'
    )
}

/// `f"{count} {word}"`, with an `s` unless the count is one.
pub fn plural(count: usize, word: &str) -> String {
    if count == 1 {
        format!("{count} {word}")
    } else {
        format!("{count} {word}s")
    }
}

#[cfg(test)]
mod tests {
    use super::{fill, repr, squash};
    use serde_json::json;

    #[test]
    fn filled_text_wraps_like_textwrap() {
        let text = "Move the helper into the package that owns it, or import it from its public module instead of reaching in.";

        assert_eq!(
            fill(text, 40, "  Hint: ", "        "),
            "  Hint: Move the helper into the package\n        that owns it, or import it from\n        its public module instead of\n        reaching in."
        );
        assert_eq!(fill("", 88, "  Hint: ", "  "), "");
        assert_eq!(
            fill("averyveryverylongword short", 10, "> ", "  "),
            "> averyveryverylongword\n  short"
        );
    }

    #[test]
    fn values_print_like_python() {
        assert_eq!(
            repr(&json!({"a": [1, true, null], "b": "it's"})),
            "{'a': [1, True, None], 'b': \"it's\"}"
        );
        assert_eq!(squash(" one\ttwo\n three "), "one two three");
    }
}
