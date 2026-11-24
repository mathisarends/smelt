//! YAML loading with the scalar rules of Smelt's strict PyYAML loader.
use regex::Regex;
use saphyr_parser::{Event, Parser, ScalarStyle, StrInput, Tag};
use std::borrow::Cow;
use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

/// A constructed YAML value. `Recursive` marks an alias to an enclosing node.
#[derive(Clone, Debug, PartialEq)]
pub enum Yaml {
    Null,
    Bool(bool),
    Int(i128),
    Float(f64),
    Str(String),
    Date(String),
    Seq(Vec<Yaml>),
    Map(Vec<(Yaml, Yaml)>),
    Recursive,
}

impl Yaml {
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::Str(value) => Some(value),
            _ => None,
        }
    }

    pub fn as_map(&self) -> Option<&[(Yaml, Yaml)]> {
        match self {
            Self::Map(pairs) => Some(pairs),
            _ => None,
        }
    }

    pub fn get(&self, key: &str) -> Option<&Yaml> {
        self.as_map()?
            .iter()
            .find(|(candidate, _)| candidate.as_str() == Some(key))
            .map(|(_, value)| value)
    }

    /// Whether two mapping keys are one key in a Python `dict`.
    pub fn same_key(&self, other: &Yaml) -> bool {
        let number = |value: &Yaml| match value {
            Self::Bool(flag) => Some(f64::from(u8::from(*flag))),
            Self::Int(integer) => Some(*integer as f64),
            Self::Float(float) => Some(*float),
            _ => None,
        };
        match (self, other) {
            (Self::Int(left), Self::Int(right)) => left == right,
            _ => match (number(self), number(other)) {
                (Some(left), Some(right)) => left == right,
                (None, None) => self == other,
                _ => false,
            },
        }
    }

    /// The value as Python's `repr` prints it in validation messages.
    pub fn repr(&self) -> String {
        match self {
            Self::Null | Self::Recursive => "None".to_owned(),
            Self::Bool(true) => "True".to_owned(),
            Self::Bool(false) => "False".to_owned(),
            Self::Int(value) => value.to_string(),
            Self::Float(value) => float_repr(*value),
            Self::Str(value) => str_repr(value),
            Self::Date(value) => timestamp_repr(value).unwrap_or_else(|| value.clone()),
            Self::Seq(items) => {
                let items: Vec<String> = items.iter().map(Self::repr).collect();
                format!("[{}]", items.join(", "))
            }
            Self::Map(pairs) => {
                let pairs: Vec<String> = pairs
                    .iter()
                    .map(|(key, value)| format!("{}: {}", key.repr(), value.repr()))
                    .collect();
                format!("{{{}}}", pairs.join(", "))
            }
        }
    }
}

fn float_repr(value: f64) -> String {
    if value.is_nan() {
        "nan".to_owned()
    } else if value.is_infinite() {
        if value > 0.0 { "inf" } else { "-inf" }.to_owned()
    } else if value.fract() == 0.0 && value.abs() < 1e16 {
        format!("{value:.1}")
    } else {
        value.to_string()
    }
}

/// Python's `repr` of a string, for the characters config values contain.
pub fn str_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut shown = String::with_capacity(value.len() + 2);
    shown.push(quote);
    for character in value.chars() {
        match character {
            '\\' => shown.push_str("\\\\"),
            '\n' => shown.push_str("\\n"),
            '\r' => shown.push_str("\\r"),
            '\t' => shown.push_str("\\t"),
            other if other == quote => {
                shown.push('\\');
                shown.push(other);
            }
            other => shown.push(other),
        }
    }
    shown.push(quote);
    shown
}

static INT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$",
    )
    .expect("the integer pattern is valid")
});
static FLOAT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
    )
    .expect("the float pattern is valid")
});
static TIMESTAMP: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)$",
    )
    .expect("the timestamp pattern is valid")
});

/// Load one document; an empty stream is `Null`, like `yaml.load`.
pub fn load(text: &str) -> Result<Yaml, String> {
    let mut builder = Builder {
        events: Parser::new_from_str(text),
        anchors: HashMap::new(),
        open: HashSet::new(),
    };
    builder.document()
}

struct Builder<'a> {
    events: Parser<'a, StrInput<'a>>,
    anchors: HashMap<usize, Yaml>,
    open: HashSet<usize>,
}

enum Entry {
    Pair(Yaml, Yaml),
    Merge(Yaml),
}

impl<'a> Builder<'a> {
    fn next(&mut self) -> Result<Event<'a>, String> {
        match self.events.next() {
            Some(Ok((event, _))) => Ok(event),
            Some(Err(error)) => Err(error.to_string()),
            None => Err("unexpected end of stream".to_owned()),
        }
    }

    fn document(&mut self) -> Result<Yaml, String> {
        let mut event = self.next()?;
        if matches!(event, Event::StreamStart) {
            event = self.next()?;
        }
        if matches!(event, Event::StreamEnd) {
            return Ok(Yaml::Null);
        }
        if !matches!(event, Event::DocumentStart(_)) {
            return Err("expected a document".to_owned());
        }
        let first = self.next()?;
        let value = self.node(first)?;
        if !matches!(self.next()?, Event::DocumentEnd) {
            return Err("expected the end of the document".to_owned());
        }
        match self.next()? {
            Event::StreamEnd => Ok(value),
            _ => Err("expected a single document in the stream".to_owned()),
        }
    }

    fn node(&mut self, event: Event<'a>) -> Result<Yaml, String> {
        match event {
            Event::Alias(id) => {
                if self.open.contains(&id) {
                    return Ok(Yaml::Recursive);
                }
                self.anchors
                    .get(&id)
                    .cloned()
                    .ok_or_else(|| "found undefined alias".to_owned())
            }
            Event::Scalar(value, style, anchor, tag) => {
                let built = scalar(&value, style, tag.as_ref())?;
                self.remember(anchor, &built);
                Ok(built)
            }
            Event::SequenceStart(anchor, tag) => {
                check_tag(tag.as_ref(), "seq")?;
                self.open.insert(anchor);
                let mut items = Vec::new();
                loop {
                    let next = self.next()?;
                    if matches!(next, Event::SequenceEnd) {
                        break;
                    }
                    items.push(self.node(next)?);
                }
                self.open.remove(&anchor);
                let built = Yaml::Seq(items);
                self.remember(anchor, &built);
                Ok(built)
            }
            Event::MappingStart(anchor, tag) => {
                check_tag(tag.as_ref(), "map")?;
                self.open.insert(anchor);
                let mut entries = Vec::new();
                loop {
                    let next = self.next()?;
                    if matches!(next, Event::MappingEnd) {
                        break;
                    }
                    let merge = matches!(
                        &next,
                        Event::Scalar(key, ScalarStyle::Plain, _, None) if key == "<<"
                    );
                    if merge {
                        let value = self.next()?;
                        entries.push(Entry::Merge(self.node(value)?));
                    } else {
                        let key = self.node(next)?;
                        if matches!(key, Yaml::Seq(_) | Yaml::Map(_)) {
                            return Err("found unhashable key".to_owned());
                        }
                        let value = self.next()?;
                        entries.push(Entry::Pair(key, self.node(value)?));
                    }
                }
                self.open.remove(&anchor);
                let built = Yaml::Map(flatten(entries)?);
                self.remember(anchor, &built);
                Ok(built)
            }
            _ => Err("expected a node".to_owned()),
        }
    }

    fn remember(&mut self, anchor: usize, value: &Yaml) {
        if anchor != 0 {
            self.anchors.insert(anchor, value.clone());
        }
    }
}

/// Merged pairs come first, so explicit keys override them in place.
fn flatten(entries: Vec<Entry>) -> Result<Vec<(Yaml, Yaml)>, String> {
    let mut merged: Vec<(Yaml, Yaml)> = Vec::new();
    let mut own: Vec<(Yaml, Yaml)> = Vec::new();
    for entry in entries {
        match entry {
            Entry::Pair(key, value) => {
                if own.iter().any(|(existing, _)| existing.same_key(&key)) {
                    return Err(format!("duplicate key {}", key.repr()));
                }
                own.push((key, value));
            }
            Entry::Merge(Yaml::Map(pairs)) => merged.extend(pairs),
            Entry::Merge(Yaml::Seq(items)) => {
                for item in items.into_iter().rev() {
                    let Yaml::Map(pairs) = item else {
                        return Err("expected a mapping for merging".to_owned());
                    };
                    merged.extend(pairs);
                }
            }
            Entry::Merge(_) => {
                return Err("expected a mapping or list of mappings for merging".to_owned());
            }
        }
    }
    let mut result: Vec<(Yaml, Yaml)> = Vec::with_capacity(merged.len() + own.len());
    for (key, value) in merged.into_iter().chain(own) {
        if let Some(slot) = result
            .iter_mut()
            .find(|(existing, _)| existing.same_key(&key))
        {
            slot.1 = value;
        } else {
            result.push((key, value));
        }
    }
    Ok(result)
}

fn check_tag(tag: Option<&Cow<'_, Tag>>, expected: &str) -> Result<(), String> {
    match tag {
        Some(tag) if !(tag.is_yaml_core_schema() && tag.suffix == expected) => Err(format!(
            "could not determine a constructor for the tag {}{}",
            tag.handle, tag.suffix
        )),
        _ => Ok(()),
    }
}

fn scalar(value: &str, style: ScalarStyle, tag: Option<&Cow<'_, Tag>>) -> Result<Yaml, String> {
    if let Some(tag) = tag {
        if !tag.is_yaml_core_schema() {
            return Err(format!(
                "could not determine a constructor for the tag {}{}",
                tag.handle, tag.suffix
            ));
        }
        return match tag.suffix.as_str() {
            "str" => Ok(Yaml::Str(value.to_owned())),
            "null" => Ok(Yaml::Null),
            "bool" => match value.to_lowercase().as_str() {
                "yes" | "true" | "on" => Ok(Yaml::Bool(true)),
                "no" | "false" | "off" => Ok(Yaml::Bool(false)),
                _ => Err(format!("invalid boolean {value}")),
            },
            "int" => integer(value)
                .map(Yaml::Int)
                .ok_or_else(|| format!("invalid integer {value}")),
            "float" => float(value)
                .map(Yaml::Float)
                .ok_or_else(|| format!("invalid float {value}")),
            other => Err(format!(
                "could not determine a constructor for the tag {}{other}",
                tag.handle
            )),
        };
    }
    if style != ScalarStyle::Plain {
        return Ok(Yaml::Str(value.to_owned()));
    }
    match value {
        "" | "~" | "null" | "Null" | "NULL" => return Ok(Yaml::Null),
        "true" | "True" | "TRUE" => return Ok(Yaml::Bool(true)),
        "false" | "False" | "FALSE" => return Ok(Yaml::Bool(false)),
        "<<" | "=" => {
            return Err("could not determine a constructor for this scalar".to_owned());
        }
        _ => {}
    }
    if FLOAT.is_match(value)
        && let Some(parsed) = float(value)
    {
        return Ok(Yaml::Float(parsed));
    }
    if INT.is_match(value)
        && let Some(parsed) = integer(value)
    {
        return Ok(Yaml::Int(parsed));
    }
    if TIMESTAMP.is_match(value) {
        return timestamp_repr(value)
            .map(|_| Yaml::Date(value.to_owned()))
            .ok_or_else(|| format!("invalid timestamp {value}"));
    }
    Ok(Yaml::Str(value.to_owned()))
}

static TIMESTAMP_PARTS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?:(?:[Tt]|[ \t]+)([0-9]{1,2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]*))?(?:[ \t]*(Z|([-+])([0-9]{1,2})(?::([0-9]{2}))?))?)?$",
    )
    .expect("the timestamp pattern is valid")
});

fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        4 | 6 | 9 | 11 => 30,
        2 if year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400)) => {
            29
        }
        2 => 28,
        _ => 31,
    }
}

/// The `repr` of the `date` or `datetime` PyYAML builds; `None` when it is invalid.
fn timestamp_repr(value: &str) -> Option<String> {
    let parts = TIMESTAMP_PARTS.captures(value)?;
    let number = |index: usize| -> Option<u32> {
        parts
            .get(index)
            .map_or(Some(0), |part| part.as_str().parse().ok())
    };
    let (year, month, day) = (number(1)?, number(2)?, number(3)?);
    if year == 0 || !(1..=12).contains(&month) || day == 0 || day > days_in_month(year, month) {
        return None;
    }
    if parts.get(4).is_none() {
        return Some(format!("datetime.date({year}, {month}, {day})"));
    }
    let (hour, minute, second) = (number(4)?, number(5)?, number(6)?);
    if hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    let fraction = parts.get(7).map_or("", |part| part.as_str());
    let digits: String = fraction.chars().chain("000000".chars()).take(6).collect();
    let micros: u32 = digits.parse().ok()?;
    let mut shown = format!("datetime.datetime({year}, {month}, {day}, {hour}, {minute}");
    if second != 0 || micros != 0 {
        shown.push_str(&format!(", {second}"));
    }
    if micros != 0 {
        shown.push_str(&format!(", {micros}"));
    }
    if let Some(zone) = parts.get(8) {
        let offset = if zone.as_str() == "Z" {
            0
        } else {
            let seconds = i64::from(number(10)?) * 3600 + i64::from(number(11)?) * 60;
            if parts.get(9)?.as_str() == "-" {
                -seconds
            } else {
                seconds
            }
        };
        if offset.abs() >= 86_400 {
            return None;
        }
        shown.push_str(", tzinfo=");
        match offset {
            0 => shown.push_str("datetime.timezone.utc"),
            positive if positive > 0 => shown.push_str(&format!(
                "datetime.timezone(datetime.timedelta(seconds={positive}))"
            )),
            negative => shown.push_str(&format!(
                "datetime.timezone(datetime.timedelta(days=-1, seconds={}))",
                86_400 + negative
            )),
        }
    }
    shown.push(')');
    Some(shown)
}

fn integer(raw: &str) -> Option<i128> {
    let text = raw.replace('_', "");
    let (sign, digits) = match text.strip_prefix('-') {
        Some(rest) => (-1, rest),
        None => (1, text.strip_prefix('+').unwrap_or(&text)),
    };
    let magnitude = if digits == "0" {
        0
    } else if let Some(binary) = digits.strip_prefix("0b") {
        i128::from_str_radix(binary, 2).ok()?
    } else if let Some(hexadecimal) = digits.strip_prefix("0x") {
        i128::from_str_radix(hexadecimal, 16).ok()?
    } else if digits.starts_with('0') {
        i128::from_str_radix(digits, 8).ok()?
    } else if digits.contains(':') {
        digits.split(':').try_fold(0_i128, |total, part| {
            part.parse::<i128>().ok().map(|value| total * 60 + value)
        })?
    } else {
        digits.parse().ok()?
    };
    Some(sign * magnitude)
}

fn float(raw: &str) -> Option<f64> {
    let text = raw.replace('_', "").to_lowercase();
    let (sign, digits) = match text.strip_prefix('-') {
        Some(rest) => (-1.0, rest),
        None => (1.0, text.strip_prefix('+').unwrap_or(&text)),
    };
    let magnitude = match digits {
        ".inf" => f64::INFINITY,
        ".nan" => f64::NAN,
        _ if digits.contains(':') => digits.split(':').try_fold(0.0, |total, part| {
            part.parse::<f64>().ok().map(|value| total * 60.0 + value)
        })?,
        _ => digits.parse().ok()?,
    };
    Some(sign * magnitude)
}

/// A composed node that keeps where each mapping key starts, like PyYAML's nodes.
#[derive(Clone)]
enum Node {
    Scalar { value: String, merge: bool },
    Seq(Vec<Node>),
    Map(Vec<(Node, (usize, usize), Node)>),
}

/// The 1-based line and column of the mapping key that `location` names.
///
/// Keys may contain dots, so each key either equals the rest of the location or
/// is a dotted prefix of it; the last matching key wins, as in a Python `dict`.
pub fn key_position(text: &str, location: &str) -> Option<(usize, usize)> {
    let mut composer = Composer {
        events: Parser::new_from_str(text),
        anchors: HashMap::new(),
    };
    find_key(&composer.document()?, location)
}

fn find_key(node: &Node, location: &str) -> Option<(usize, usize)> {
    let Node::Map(pairs) = node else {
        return None;
    };
    for (key, position, child) in flattened(pairs).into_iter().rev() {
        let Node::Scalar { value, .. } = key else {
            continue;
        };
        if value == location {
            return Some(position);
        }
        if let Some(rest) = location
            .strip_prefix(value.as_str())
            .and_then(|rest| rest.strip_prefix('.'))
            && let Some(found) = find_key(&child, rest)
        {
            return Some(found);
        }
    }
    None
}

/// Resolve `<<` merge keys: merged pairs come first, later sources first.
fn flattened(pairs: &[(Node, (usize, usize), Node)]) -> Vec<(Node, (usize, usize), Node)> {
    let mut merged = Vec::new();
    let mut own = Vec::new();
    for (key, position, value) in pairs {
        if !matches!(key, Node::Scalar { merge: true, .. }) {
            own.push((key.clone(), *position, value.clone()));
            continue;
        }
        match value {
            Node::Map(inner) => merged.extend(flattened(inner)),
            Node::Seq(items) => {
                for item in items.iter().rev() {
                    if let Node::Map(inner) = item {
                        merged.extend(flattened(inner));
                    }
                }
            }
            Node::Scalar { .. } => {}
        }
    }
    merged.extend(own);
    merged
}

struct Composer<'a> {
    events: Parser<'a, StrInput<'a>>,
    anchors: HashMap<usize, Node>,
}

impl<'a> Composer<'a> {
    fn next(&mut self) -> Option<(Event<'a>, (usize, usize))> {
        let (event, span) = self.events.next()?.ok()?;
        Some((event, (span.start.line(), span.start.col() + 1)))
    }

    fn document(&mut self) -> Option<Node> {
        let (mut event, _) = self.next()?;
        if matches!(event, Event::StreamStart) {
            event = self.next()?.0;
        }
        if !matches!(event, Event::DocumentStart(_)) {
            return None;
        }
        let first = self.next()?.0;
        let node = self.node(first)?;
        if !matches!(self.next()?.0, Event::DocumentEnd) {
            return None;
        }
        matches!(self.next()?.0, Event::StreamEnd).then_some(node)
    }

    fn node(&mut self, event: Event<'a>) -> Option<Node> {
        let (node, anchor) = match event {
            Event::Alias(id) => return self.anchors.get(&id).cloned(),
            Event::Scalar(value, style, anchor, tag) => (
                Node::Scalar {
                    merge: style == ScalarStyle::Plain && tag.is_none() && value == "<<",
                    value: value.into_owned(),
                },
                anchor,
            ),
            Event::SequenceStart(anchor, _) => {
                let mut items = Vec::new();
                loop {
                    let (next, _) = self.next()?;
                    if matches!(next, Event::SequenceEnd) {
                        break;
                    }
                    items.push(self.node(next)?);
                }
                (Node::Seq(items), anchor)
            }
            Event::MappingStart(anchor, _) => {
                let mut pairs = Vec::new();
                loop {
                    let (next, position) = self.next()?;
                    if matches!(next, Event::MappingEnd) {
                        break;
                    }
                    let key = self.node(next)?;
                    let value = self.next()?.0;
                    pairs.push((key, position, self.node(value)?));
                }
                (Node::Map(pairs), anchor)
            }
            _ => return None,
        };
        if anchor != 0 {
            self.anchors.insert(anchor, node.clone());
        }
        Some(node)
    }
}

#[cfg(test)]
mod tests {
    use super::key_position;

    #[test]
    fn key_position_follows_dotted_keys_and_merges() {
        let text = "base: &base\n  ARC007: error\nrules:\n  <<: *base\n  \"a/b.c\": hint\n";
        assert_eq!(key_position(text, "rules.a/b.c"), Some((5, 3)));
        // A merged key keeps the position where the anchor spells it.
        assert_eq!(key_position(text, "rules.ARC007"), Some((2, 3)));
        assert_eq!(key_position(text, "rules.missing"), None);
        assert_eq!(key_position("[unclosed", "rules"), None);
    }
}
