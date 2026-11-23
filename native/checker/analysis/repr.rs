//! CPython's `repr` for the constants that appear in findings.
use super::printable::NON_PRINTABLE;
use ruff_python_ast::Int;
use std::cmp::Ordering;
use std::fmt::Write;

/// `str.isprintable` for one character.
pub fn is_printable(character: char) -> bool {
    let code = u32::from(character);
    NON_PRINTABLE
        .binary_search_by(|&(start, end)| {
            if code < start {
                Ordering::Greater
            } else if code > end {
                Ordering::Less
            } else {
                Ordering::Equal
            }
        })
        .is_err()
}

/// `repr(str)`.
pub fn str_repr(text: &str) -> String {
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
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            _ if character == quote => {
                out.push('\\');
                out.push(character);
            }
            _ if is_printable(character) => out.push(character),
            _ => push_code_escape(&mut out, character),
        }
    }
    out.push(quote);
    out
}

/// `character.encode("unicode_escape")` for a backslash or non-printable character.
pub fn unicode_escape(character: char) -> String {
    let mut out = String::new();
    match character {
        '\\' => out.push_str("\\\\"),
        '\t' => out.push_str("\\t"),
        '\n' => out.push_str("\\n"),
        '\r' => out.push_str("\\r"),
        _ => push_code_escape(&mut out, character),
    }
    out
}

fn push_code_escape(out: &mut String, character: char) {
    let code = u32::from(character);
    let written = if code <= 0xff {
        write!(out, "\\x{code:02x}")
    } else if code <= 0xffff {
        write!(out, "\\u{code:04x}")
    } else {
        write!(out, "\\U{code:08x}")
    };
    written.expect("writing to a string cannot fail");
}

/// `repr(bytes)`.
pub fn bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') {
        b'"'
    } else {
        b'\''
    };
    let mut out = String::with_capacity(bytes.len() + 3);
    out.push('b');
    out.push(char::from(quote));
    for &byte in bytes {
        match byte {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            _ if byte == quote => {
                out.push('\\');
                out.push(char::from(byte));
            }
            0x20..=0x7e => out.push(char::from(byte)),
            _ => write!(out, "\\x{byte:02x}").expect("writing to a string cannot fail"),
        }
    }
    out.push(char::from(quote));
    out
}

/// `repr(float)`: the shortest digits, in exponent form outside 1e-4..1e16.
pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_owned();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf" } else { "-inf" }.to_owned();
    }
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .to_owned();
    }
    let scientific = format!("{:e}", value.abs());
    let (mantissa, exponent) = scientific
        .split_once('e')
        .expect("scientific notation has an exponent");
    let digits: String = mantissa.chars().filter(|item| *item != '.').collect();
    let exponent: i32 = exponent.parse().expect("the exponent is an integer");
    let point = exponent + 1;
    let sign = if value < 0.0 { "-" } else { "" };
    if !(-3..=16).contains(&point) {
        let (first, rest) = digits.split_at(1);
        let fraction = if rest.is_empty() {
            String::new()
        } else {
            format!(".{rest}")
        };
        let direction = if exponent < 0 { '-' } else { '+' };
        return format!(
            "{sign}{first}{fraction}e{direction}{:02}",
            exponent.unsigned_abs()
        );
    }
    let length = i32::try_from(digits.len()).expect("a float has few digits");
    if point <= 0 {
        let zeros = "0".repeat(point.unsigned_abs() as usize);
        format!("{sign}0.{zeros}{digits}")
    } else if point >= length {
        let zeros = "0".repeat((point - length) as usize);
        format!("{sign}{digits}{zeros}.0")
    } else {
        let (whole, fraction) = digits.split_at(point as usize);
        format!("{sign}{whole}.{fraction}")
    }
}

/// `repr` of an imaginary literal, whose real part is always zero.
pub fn imaginary_repr(value: f64) -> String {
    let text = float_repr(value);
    format!("{}j", text.strip_suffix(".0").unwrap_or(&text))
}

/// `repr(int)` for a literal; Ruff keeps the source of those above `u64`.
pub fn int_repr(value: &Int) -> String {
    let text = value.to_string();
    let lowered = text.to_ascii_lowercase();
    let (digits, radix) = if let Some(digits) = lowered.strip_prefix("0x") {
        (digits, 16)
    } else if let Some(digits) = lowered.strip_prefix("0o") {
        (digits, 8)
    } else if let Some(digits) = lowered.strip_prefix("0b") {
        (digits, 2)
    } else {
        return text;
    };
    // Base 1e9 limbs, least significant first.
    let mut limbs: Vec<u64> = vec![0];
    for digit in digits.chars().filter_map(|item| item.to_digit(radix)) {
        let mut carry = u64::from(digit);
        for limb in &mut limbs {
            let next = *limb * u64::from(radix) + carry;
            *limb = next % 1_000_000_000;
            carry = next / 1_000_000_000;
        }
        if carry > 0 {
            limbs.push(carry);
        }
    }
    let mut parts = limbs.iter().rev();
    let mut out = parts.next().map_or_else(String::new, u64::to_string);
    for limb in parts {
        write!(out, "{limb:09}").expect("writing to a string cannot fail");
    }
    out
}

/// `repr` of a tuple of strings.
pub fn str_tuple_repr(items: &[String]) -> String {
    let rendered: Vec<String> = items.iter().map(|item| str_repr(item)).collect();
    if let [single] = rendered.as_slice() {
        format!("({single},)")
    } else {
        format!("({})", rendered.join(", "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_switch_to_exponents_where_python_does() {
        for (value, expected) in [
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e300, "1.5e+300"),
            (2.5, "2.5"),
            (100.0, "100.0"),
        ] {
            assert_eq!(float_repr(value), expected);
        }
    }

    #[test]
    fn strings_choose_quotes_like_python() {
        assert_eq!(str_repr("a'b"), "\"a'b\"");
        assert_eq!(str_repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(str_repr("\u{7f}\u{ad}é"), "'\\x7f\\xadé'");
    }
}
