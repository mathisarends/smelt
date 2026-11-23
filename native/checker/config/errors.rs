//! Configuration issues, rendered like `smelt.config.errors.ConfigError`.
use std::collections::{BTreeSet, HashMap};
use std::path::{Component, Path};

/// The guide linked from messages about retired configuration.
pub fn upgrade_guide_url() -> String {
    format!(
        "https://github.com/mathisarends/smelt/blob/v{}/docs/upgrading.md",
        env!("CARGO_PKG_VERSION")
    )
}

#[derive(Clone, Debug, PartialEq)]
pub struct Issue {
    pub path: String,
    pub message: String,
    pub source: Option<String>,
    pub semantic: bool,
}

impl Issue {
    pub fn new(path: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            message: message.into(),
            source: None,
            semantic: false,
        }
    }

    fn line(&self, source: Option<&str>) -> String {
        let detail = if self.path.is_empty() {
            self.message.clone()
        } else {
            format!("{}: {}", self.path, self.message)
        };
        match source {
            Some(source) => format!("{source}: {detail}"),
            None => detail,
        }
    }
}

#[derive(Clone, Debug)]
pub struct ConfigError {
    pub issues: Vec<Issue>,
    pub source: Option<String>,
}

impl ConfigError {
    pub fn new(issues: Vec<Issue>, source: Option<String>) -> Self {
        Self { issues, source }
    }

    pub fn one(path: &str, message: impl Into<String>, source: &str) -> Self {
        Self::new(vec![Issue::new(path, message)], Some(source.to_owned()))
    }

    pub fn render(&self) -> String {
        let guide_url = upgrade_guide_url();
        let see = format!(" (see {guide_url})");
        let semi = format!("; see {guide_url}");
        let mut lines = vec![match &self.source {
            Some(source) => format!("invalid configuration in {source}"),
            None => "invalid configuration".to_owned(),
        }];
        let mut guide = false;
        for issue in &self.issues {
            let trimmed = issue.message.strip_suffix(&see).unwrap_or(&issue.message);
            let message = trimmed.strip_suffix(&semi).unwrap_or(trimmed);
            guide |= message != issue.message;
            let source = issue
                .source
                .as_deref()
                .filter(|shown| self.source.as_deref() != Some(*shown));
            let visible = Issue {
                message: message.to_owned(),
                ..issue.clone()
            };
            lines.push(format!("  {}", visible.line(source)));
        }
        if guide {
            lines.push(format!("  see {guide_url}"));
        }
        lines.join("\n")
    }

    /// Show declaring files relative to the project, once when all issues agree.
    pub fn relative_to(self, root: &Path) -> Self {
        let issues: Vec<Issue> = self
            .issues
            .into_iter()
            .map(|issue| Issue {
                source: issue.source.map(|source| relative_source(&source, root)),
                ..issue
            })
            .collect();
        let source = self.source.map(|source| relative_source(&source, root));
        let files: BTreeSet<Option<&String>> = issues
            .iter()
            .map(|issue| issue.source.as_ref().or(source.as_ref()))
            .collect();
        let shared = (files.len() == 1).then(|| files.into_iter().next().flatten().cloned());
        let source = shared.unwrap_or(source);
        Self { issues, source }
    }
}

fn relative_source(source: &str, root: &Path) -> String {
    let path = Path::new(source);
    if !path.is_absolute() {
        return source.to_owned();
    }
    relative_path(path, root).unwrap_or_else(|| source.to_owned())
}

/// `os.path.relpath` for two absolute paths, in POSIX spelling.
fn relative_path(path: &Path, root: &Path) -> Option<String> {
    let key = |component: &Component<'_>| {
        let text = component.as_os_str().to_string_lossy().replace('\\', "/");
        if cfg!(windows) {
            text.to_lowercase()
        } else {
            text
        }
    };
    let from: Vec<Component<'_>> = root.components().collect();
    let to: Vec<Component<'_>> = path.components().collect();
    if from.first().map(&key) != to.first().map(&key) {
        return None;
    }
    let common = from
        .iter()
        .zip(&to)
        .take_while(|(left, right)| key(left) == key(right))
        .count();
    let mut parts: Vec<String> = vec!["..".to_owned(); from.len() - common];
    parts.extend(
        to[common..]
            .iter()
            .map(|part| part.as_os_str().to_string_lossy().into_owned()),
    );
    Some(if parts.is_empty() {
        ".".to_owned()
    } else {
        parts.join("/")
    })
}

/// One step of a validation location.
#[derive(Clone, Debug, PartialEq)]
pub enum Segment {
    Key(String),
    Index(usize),
    /// A non-string mapping key that Python renders like an index, such as `-1`.
    Bracket(String),
}

/// Render a location as `a.b[1].c`.
pub fn format_loc(location: &[Segment]) -> String {
    let mut shown = String::new();
    for segment in location {
        match segment {
            Segment::Index(index) => shown.push_str(&format!("[{index}]")),
            Segment::Bracket(key) => shown.push_str(&format!("[{key}]")),
            Segment::Key(key) => {
                if !shown.is_empty() {
                    shown.push('.');
                }
                shown.push_str(key);
            }
        }
    }
    shown
}

pub fn did_you_mean<S: AsRef<str>>(value: &str, candidates: &[S]) -> String {
    let mut options: Vec<&str> = candidates.iter().map(AsRef::as_ref).collect();
    options.sort_unstable();
    let word: Vec<char> = value.chars().collect();
    let mut best: Option<(f64, &str)> = None;
    for option in &options {
        let score = ratio(&option.chars().collect::<Vec<_>>(), &word);
        if score >= 0.6
            && best.is_none_or(|(top, name)| score > top || (score == top && *option > name))
        {
            best = Some((score, option));
        }
    }
    let matched = best.map(|(_, name)| name).or_else(|| {
        options
            .iter()
            .find(|option| option.starts_with(value))
            .copied()
    });
    matched.map_or_else(String::new, |name| format!(" (did you mean \"{name}\"?)"))
}

/// `difflib.SequenceMatcher(a=left, b=right).ratio()`.
fn ratio(left: &[char], right: &[char]) -> f64 {
    let total = left.len() + right.len();
    if total == 0 {
        return 1.0;
    }
    let mut positions: HashMap<char, Vec<usize>> = HashMap::new();
    for (index, character) in right.iter().enumerate() {
        positions.entry(*character).or_default().push(index);
    }
    let mut matches = 0;
    let mut queue = vec![(0, left.len(), 0, right.len())];
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (start_a, start_b, size) = longest_match(left, &positions, (alo, ahi), (blo, bhi));
        if size == 0 {
            continue;
        }
        matches += size;
        if alo < start_a && blo < start_b {
            queue.push((alo, start_a, blo, start_b));
        }
        if start_a + size < ahi && start_b + size < bhi {
            queue.push((start_a + size, ahi, start_b + size, bhi));
        }
    }
    2.0 * matches as f64 / total as f64
}

fn longest_match(
    left: &[char],
    positions: &HashMap<char, Vec<usize>>,
    (alo, ahi): (usize, usize),
    (blo, bhi): (usize, usize),
) -> (usize, usize, usize) {
    let (mut best_a, mut best_b, mut best) = (alo, blo, 0);
    let mut lengths: HashMap<usize, usize> = HashMap::new();
    for (index, character) in left.iter().enumerate().take(ahi).skip(alo) {
        let mut next: HashMap<usize, usize> = HashMap::new();
        for &position in positions.get(character).map_or(&[][..], Vec::as_slice) {
            if position < blo {
                continue;
            }
            if position >= bhi {
                break;
            }
            let previous = position
                .checked_sub(1)
                .and_then(|before| lengths.get(&before))
                .copied()
                .unwrap_or(0);
            let size = previous + 1;
            next.insert(position, size);
            if size > best {
                best_a = index + 1 - size;
                best_b = position + 1 - size;
                best = size;
            }
        }
        lengths = next;
    }
    (best_a, best_b, best)
}
