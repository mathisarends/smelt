//! What `--changed` compares with, as `smelt.engine.changes` resolves it.
use serde_json::{Map, Value, json};
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Git variables that leave repository discovery and object lookup alone,
/// including those Git sets for a commit hook.
const HARMLESS: [&str; 7] = [
    "GIT_PAGER",
    "GIT_OPTIONAL_LOCKS",
    "GIT_INDEX_FILE",
    "GIT_EDITOR",
    "GIT_EXEC_PATH",
    "GIT_PREFIX",
    "GIT_REFLOG_ACTION",
];

/// What a `--changed` check compared the working tree with.
pub struct Comparison {
    /// `--base` as given.
    pub base: Option<String>,
    /// The commit compared with; `None` before the first commit.
    pub revision: Option<String>,
    pub head: Option<String>,
}

impl Comparison {
    /// `Comparison.to_json`.
    pub fn to_json(&self) -> Map<String, Value> {
        let mut comparison = Map::new();
        comparison.insert("mode".into(), json!("changed"));
        comparison.insert("base".into(), json!(self.base));
        comparison.insert(
            "merge_base".into(),
            json!(self.base.as_ref().and(self.revision.as_ref())),
        );
        comparison.insert("compared_revision".into(), json!(self.revision));
        comparison.insert("head".into(), json!(self.head));
        comparison.insert("working_tree".into(), json!(true));
        comparison
    }
}

/// `resolve_comparison`: the repository's top level and what a check of
/// `root` compares with; `Err` where the Python engine would fail.
pub fn resolve(root: &Path, base: Option<&str>) -> Result<(PathBuf, Comparison), &'static str> {
    let (top, head) = repository(root)?;
    let revision = match base {
        Some(base) => {
            let output = git(
                root,
                &["merge-base", head.as_deref().unwrap_or("HEAD"), base],
            )
            .filter(|output| output.status.success())
            .ok_or("a merge base Git cannot find")?;
            Some(
                String::from_utf8(output.stdout)
                    .map_err(|_| "an unreadable merge base")?
                    .trim()
                    .to_owned(),
            )
        }
        None => head.clone(),
    };
    Ok((
        top,
        Comparison {
            base: base.map(str::to_owned),
            revision,
            head,
        },
    ))
}

/// The top level of the repository `anchor` lies in, and its HEAD commit,
/// `None` before the first commit.
fn repository(anchor: &Path) -> Result<(PathBuf, Option<String>), &'static str> {
    let foreign = env::vars_os().any(|(name, _)| {
        name.to_str().is_none_or(|name| {
            name.starts_with("GIT_")
                && !HARMLESS.contains(&name)
                && !name.starts_with("GIT_AUTHOR_")
                && !name.starts_with("GIT_COMMITTER_")
        })
    });
    if foreign {
        // Git reads these; ask it rather than interpret them.
        return revparse(anchor);
    }
    let repo = gix::discover_opts(
        anchor,
        Default::default(),
        gix::open::Options::default().bail_if_untrusted(true),
    )
    .map_err(|_| "no Git repository")?;
    let top = repo.workdir().ok_or("a bare Git repository")?.to_path_buf();
    let head = repo
        .head()
        .map_err(|_| "an unreadable Git HEAD")?
        .id()
        .map(|id| id.to_string());
    Ok((top, head))
}

/// `git rev-parse --show-toplevel --verify --quiet HEAD`, as `capture_repository` runs it.
fn revparse(anchor: &Path) -> Result<(PathBuf, Option<String>), &'static str> {
    let output = git(
        anchor,
        &[
            "rev-parse",
            "--show-toplevel",
            "--verify",
            "--quiet",
            "HEAD",
        ],
    )
    .ok_or("no Git")?;
    let text = String::from_utf8(output.stdout).map_err(|_| "a non-UTF-8 repository path")?;
    let mut lines = text.lines();
    let top = lines.next().ok_or("no Git repository")?;
    match output.status.code() {
        Some(0) => Ok((
            PathBuf::from(top),
            Some(lines.next().ok_or("no Git HEAD")?.to_owned()),
        )),
        Some(1) => Ok((PathBuf::from(top), None)),
        _ => Err("no Git repository"),
    }
}

fn git(directory: &Path, arguments: &[&str]) -> Option<std::process::Output> {
    Command::new("git")
        .args(arguments)
        .current_dir(directory)
        .stdin(Stdio::null())
        .output()
        .ok()
}
