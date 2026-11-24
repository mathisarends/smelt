//! Paths, globs and file reads with the behavior of `pathlib` on this platform.
use std::env;
use std::ffi::OsStr;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

/// `Path.resolve()`: follow what exists and keep the missing tail.
pub fn resolve(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        env::current_dir().unwrap_or_default().join(path)
    };
    let mut existing = absolute.as_path();
    let mut missing: Vec<&OsStr> = Vec::new();
    loop {
        if let Ok(real) = fs::canonicalize(existing) {
            let mut resolved = without_verbatim(real);
            for part in missing.iter().rev() {
                resolved.push(part);
            }
            return resolved;
        }
        match (existing.parent(), existing.file_name()) {
            (Some(parent), Some(name)) => {
                missing.push(name);
                existing = parent;
            }
            _ => return lexical(&absolute),
        }
    }
}

fn lexical(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                result.pop();
            }
            other => result.push(other.as_os_str()),
        }
    }
    result
}

fn without_verbatim(path: PathBuf) -> PathBuf {
    if !cfg!(windows) {
        return path;
    }
    let text = path.to_string_lossy();
    if let Some(share) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{share}"));
    }
    match text.strip_prefix(r"\\?\") {
        Some(plain) => PathBuf::from(plain),
        None => path,
    }
}

/// `Path.as_posix()`.
pub fn posix(path: &Path) -> String {
    let text = path.to_string_lossy();
    if cfg!(windows) {
        text.replace('\\', "/")
    } else {
        text.into_owned()
    }
}

/// Whether two paths are equal under this platform's path comparison.
pub fn same_path(left: &Path, right: &Path) -> bool {
    if cfg!(windows) {
        posix(left).to_lowercase() == posix(right).to_lowercase()
    } else {
        left == right
    }
}

/// UTF-8 text with universal newlines; invalid text is an `InvalidData` error.
pub fn read_text(path: &Path) -> io::Result<String> {
    let bytes = fs::read(path)?;
    let text = String::from_utf8(bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    Ok(text.replace("\r\n", "\n").replace('\r', "\n"))
}

/// Direct children; an unreadable directory has none.
pub fn children(directory: &Path) -> Vec<PathBuf> {
    fs::read_dir(directory)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .collect()
        })
        .unwrap_or_default()
}

/// `root.glob(pattern)` for a relative pattern.
pub fn glob(root: &Path, pattern: &str) -> Vec<PathBuf> {
    let separators: &[char] = if cfg!(windows) { &['/', '\\'] } else { &['/'] };
    if pattern.starts_with(separators) || Path::new(pattern).has_root() {
        return Vec::new();
    }
    let parts: Vec<&str> = pattern
        .split(separators)
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    let mut found = Vec::new();
    if !parts.is_empty() {
        select(root, &parts, &mut found);
    }
    found
}

fn select(directory: &Path, parts: &[&str], found: &mut Vec<PathBuf>) {
    let Some((part, rest)) = parts.split_first() else {
        found.push(directory.to_path_buf());
        return;
    };
    if *part == "**" {
        select(directory, rest, found);
        for child in children(directory) {
            if is_real_directory(&child) {
                select(&child, parts, found);
            }
        }
    } else if part.contains(['*', '?', '[']) {
        for child in children(directory) {
            let matched = child
                .file_name()
                .is_some_and(|name| name_matches(part, &name.to_string_lossy()));
            if matched && (rest.is_empty() || child.is_dir()) {
                select(&child, rest, found);
            }
        }
    } else {
        let next = directory.join(part);
        if next.exists() {
            select(&next, rest, found);
        }
    }
}

fn is_real_directory(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_dir())
}

/// Whether `directory` holds, at any depth, an entry matching `pattern`.
pub fn contains_match(directory: &Path, pattern: &str) -> bool {
    children(directory).iter().any(|child| {
        child
            .file_name()
            .is_some_and(|name| name_matches(pattern, &name.to_string_lossy()))
            || (is_real_directory(child) && contains_match(child, pattern))
    })
}

/// Whether `directory` has a direct child matching `pattern`.
pub fn has_child_match(directory: &Path, pattern: &str) -> bool {
    children(directory).iter().any(|child| {
        child
            .file_name()
            .is_some_and(|name| name_matches(pattern, &name.to_string_lossy()))
    })
}

/// `fnmatch.fnmatchcase`: `*` also matches `/`.
pub fn fnmatchcase(pattern: &str, name: &str) -> bool {
    wildcard(
        &pattern.chars().collect::<Vec<_>>(),
        &name.chars().collect::<Vec<_>>(),
    )
}

/// `fnmatch` for one path segment, ignoring case on Windows.
pub fn name_matches(pattern: &str, name: &str) -> bool {
    let fold = |text: &str| -> Vec<char> {
        if cfg!(windows) {
            text.to_lowercase().chars().collect()
        } else {
            text.chars().collect()
        }
    };
    wildcard(&fold(pattern), &fold(name))
}

fn wildcard(pattern: &[char], text: &[char]) -> bool {
    let Some((first, rest)) = pattern.split_first() else {
        return text.is_empty();
    };
    match first {
        '*' => (0..=text.len()).any(|skip| wildcard(rest, &text[skip..])),
        '?' => !text.is_empty() && wildcard(rest, &text[1..]),
        '[' => match character_class(rest) {
            Some((negated, members, after)) => text.split_first().is_some_and(|(next, tail)| {
                class_contains(&members, *next) != negated && wildcard(after, tail)
            }),
            None => text.first() == Some(&'[') && wildcard(rest, &text[1..]),
        },
        literal => text.first() == Some(literal) && wildcard(rest, &text[1..]),
    }
}

fn character_class(pattern: &[char]) -> Option<(bool, Vec<char>, &[char])> {
    let negated = pattern.first() == Some(&'!');
    let start = usize::from(negated);
    let mut end = start;
    if pattern.get(end) == Some(&']') {
        end += 1;
    }
    while pattern.get(end).is_some_and(|character| *character != ']') {
        end += 1;
    }
    pattern
        .get(end)
        .map(|_| (negated, pattern[start..end].to_vec(), &pattern[end + 1..]))
}

fn class_contains(members: &[char], candidate: char) -> bool {
    let mut index = 0;
    while index < members.len() {
        if index + 2 < members.len() && members[index + 1] == '-' {
            if members[index] <= candidate && candidate <= members[index + 2] {
                return true;
            }
            index += 3;
        } else {
            if members[index] == candidate {
                return true;
            }
            index += 1;
        }
    }
    false
}
