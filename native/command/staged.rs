//! A `--staged` check's project, read from the Git index as
//! `smelt.engine.staged` reads it.
use super::changes;
use crate::checker::config::load::LoadedConfig;
use crate::checker::config::paths;
use crate::checker::snapshot::{self, Snapshot};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::env;
use std::fmt::Write;
use std::path::{Component, Path, PathBuf};

const SUBMODULE: &str = "160000";
const SYMBOLIC_LINK: &str = "120000";

/// The staged index, mounted where the check reads its project.
pub struct Staged {
    pub snapshot: Snapshot,
    /// The repository's top level in the working tree.
    pub repository: PathBuf,
    pub head: Option<String>,
    /// `GitIndex.identity`: the SHA-256 of the index records.
    pub identity: String,
    /// The blob ID of each staged file, by repository-relative path.
    blobs: BTreeMap<String, String>,
}

impl Staged {
    /// `capture_repository` and `capture_index` from `cwd`; `Err` where the
    /// Python engine would fail or Git variables could change what it reads.
    pub fn capture(cwd: &Path) -> Result<Self, &'static str> {
        if changes::foreign_git_variables() {
            return Err("Git variables that change the index Git reads");
        }
        let index_file = env::var_os("GIT_INDEX_FILE").map(PathBuf::from);
        let captured = crate::git::capture(cwd, index_file.as_deref())
            .map_err(|_| "an index the native Git reader cannot read")?;
        let repository = paths::resolve(&captured.repository);
        let mut blobs = BTreeMap::new();
        for record in captured.records.split(|byte| *byte == 0) {
            if record.is_empty() {
                continue;
            }
            let record = std::str::from_utf8(record).map_err(|_| "a non-UTF-8 staged path")?;
            let (metadata, path) = record.split_once('\t').ok_or("an unreadable index")?;
            let mut fields = metadata.split(' ');
            let (Some(mode), Some(id), Some(stage)) = (fields.next(), fields.next(), fields.next())
            else {
                return Err("an unreadable index");
            };
            if stage != "0" {
                return Err("an unmerged index");
            }
            // A link blob holds the target name, not a copy of its contents.
            if mode != SYMBOLIC_LINK && mode != SUBMODULE {
                blobs.insert(path.to_owned(), id.to_owned());
            }
        }
        let snapshot = Snapshot::index(&repository, &blobs).map_err(|_| "an unsafe staged path")?;
        let mut identity = String::with_capacity(64);
        for byte in Sha256::digest(&captured.records) {
            let _ = write!(identity, "{byte:02x}");
        }
        Ok(Self {
            snapshot,
            repository,
            head: captured.head,
            identity,
            blobs,
        })
    }

    /// Where the working-tree `path` lies in the staged snapshot.
    pub fn locate(&self, path: &Path) -> Result<PathBuf, &'static str> {
        self.snapshot
            .locate(&self.repository, path)
            .map_err(|_| "a path outside the Git repository")
    }

    /// The working-tree path of the snapshot's `path`.
    pub fn original(&self, path: &Path) -> PathBuf {
        path.strip_prefix(self.snapshot.tree()).map_or_else(
            |_| path.to_path_buf(),
            |relative| self.repository.join(relative),
        )
    }

    /// Whether the snapshot's `path`, with `..` applied, stays in the repository.
    pub fn contains(&self, path: &Path) -> bool {
        lexical(path).starts_with(self.snapshot.tree())
    }

    /// The staged content of the snapshot's file at `path`; `None` when the
    /// index does not stage it.
    pub fn read(&self, path: &Path) -> Option<Result<Vec<u8>, ()>> {
        let relative = lexical(path)
            .strip_prefix(self.snapshot.tree())
            .ok()
            .map(paths::posix)?;
        let id = self.blobs.get(&relative)?;
        Some(snapshot::blob(&self.repository, id).map_err(|_| ()))
    }

    /// The paths a staged check's project must keep inside the repository.
    pub fn validate(&self, loaded: &LoadedConfig, debt: &[String]) -> Result<(), &'static str> {
        let root = loaded.path.parent().ok_or("no project root")?;
        let roots = ["source_roots", "test_roots"].into_iter().flat_map(|key| {
            loaded.config["project"][key]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|directory| directory.as_str())
        });
        for directory in roots {
            if !self.contains(&root.join(directory)) {
                return Err("a staged source or test root outside the repository");
            }
        }
        if debt.iter().any(|path| !self.contains(&root.join(path))) {
            return Err("a staged debt file outside the repository");
        }
        Ok(())
    }
}

/// `os.path.abspath` for an absolute path: `..` applied without the filesystem.
pub fn lexical(path: &Path) -> PathBuf {
    let mut normal = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir => {
                normal.pop();
            }
            Component::CurDir => {}
            other => normal.push(other),
        }
    }
    normal
}
