//! A Git snapshot written to a temporary directory, so the core checks the
//! staged index or a base revision like any other working tree.
//!
//! Only the files the core reads (Python sources, YAML and TOML) carry their
//! content; every other file is written empty, so its directory still exists
//! without the cost of large assets.
use rayon::prelude::*;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// The suffixes of files whose content the core reads.
const READ_SUFFIXES: [&str; 5] = [".py", ".pyi", ".yml", ".yaml", ".toml"];
const SUBMODULE: u32 = 0o160000;
const SYMBOLIC_LINK: u32 = 0o120000;
/// Decoded objects each thread keeps, so blobs sharing delta bases decode once.
const OBJECT_CACHE_BYTES: usize = 32 * 1024 * 1024;

static SEQUENCE: AtomicUsize = AtomicUsize::new(0);

/// A repository's files at one Git state, removed with this value.
pub struct Snapshot {
    directory: PathBuf,
    /// The repository's top level inside `directory`.
    tree: PathBuf,
}

impl Snapshot {
    /// The staged index, as blob IDs by repository-relative path.
    pub fn index(repository: &Path, blobs: &BTreeMap<String, String>) -> Result<Self, String> {
        let entries = blobs
            .iter()
            .map(|(path, id)| Ok((safe_path(path)?, object_id(id)?)))
            .collect::<Result<Vec<_>, String>>()?;
        Self::write(repository, entries)
    }

    /// The tree of a commit.
    pub fn revision(repository: &Path, revision: &str) -> Result<Self, String> {
        let repo = open(repository)?;
        let commit = repo
            .find_commit(object_id(revision)?)
            .map_err(|error| format!("cannot read revision {revision}: {error}"))?;
        let files = commit
            .tree()
            .and_then(|tree| tree.traverse().breadthfirst.files())
            .map_err(|error| format!("cannot read revision {revision}: {error}"))?;
        let mut entries = Vec::new();
        for entry in files {
            let path = String::from_utf8(entry.filepath.to_vec())
                .map_err(|_| "non-UTF-8 path in Git snapshot".to_owned())?;
            let mode = entry.mode.value() as u32;
            if entry.mode.is_tree() || matches!(mode, SUBMODULE | SYMBOLIC_LINK) {
                continue;
            }
            entries.push((safe_path(&path)?, entry.oid));
        }
        Self::write(repository, entries)
    }

    /// Where `path`, inside the repository whose top level is `top`, lies in this snapshot.
    pub fn locate(&self, top: &Path, path: &Path) -> Result<PathBuf, String> {
        let top = canonical(top);
        let path = canonical(path);
        let relative = path
            .strip_prefix(&top)
            .map_err(|_| format!("{} is outside the Git repository", path.display()))?;
        Ok(self.tree.join(relative))
    }

    /// The snapshot's copy of the repository's top level.
    pub fn tree(&self) -> &Path {
        &self.tree
    }

    fn write(repository: &Path, entries: Vec<(String, gix::ObjectId)>) -> Result<Self, String> {
        let directory = std::env::temp_dir().join(format!(
            "smelt-snapshot-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |time| time.as_nanos()),
            SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        // The nested top level keeps a configured `../` root inside the snapshot.
        let tree = directory.join("repository");
        fs::create_dir_all(&tree).map_err(|error| error.to_string())?;
        let snapshot = Self {
            tree: canonical(&tree),
            directory,
        };
        let directories: BTreeSet<&Path> = entries
            .iter()
            .filter_map(|(path, _)| Path::new(path).parent())
            .collect();
        for directory in directories {
            fs::create_dir_all(snapshot.tree.join(directory)).map_err(|error| error.to_string())?;
        }
        // A repository handle is not shared between threads, so each share of the
        // files opens its own, with a cache for the deltas packed blobs share.
        let share = entries.len().div_ceil(rayon::current_num_threads()).max(1);
        entries
            .par_chunks(share)
            .map(|files| {
                let mut repo = open(repository)?;
                repo.object_cache_size_if_unset(OBJECT_CACHE_BYTES);
                files
                    .iter()
                    .try_for_each(|(path, id)| snapshot.write_file(&repo, path, *id))
            })
            .collect::<Result<(), String>>()?;
        Ok(snapshot)
    }

    fn write_file(
        &self,
        repo: &gix::Repository,
        path: &str,
        id: gix::ObjectId,
    ) -> Result<(), String> {
        let target = self.tree.join(path);
        let content = if READ_SUFFIXES.iter().any(|suffix| path.ends_with(suffix)) {
            repo.find_blob(id)
                .map_err(|error| format!("cannot read {path} from Git: {error}"))?
                .take_data()
        } else {
            Vec::new()
        };
        fs::write(&target, content).map_err(|error| format!("cannot write {path}: {error}"))
    }
}

impl Drop for Snapshot {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}

fn open(repository: &Path) -> Result<gix::Repository, String> {
    gix::open_opts(
        repository,
        gix::open::Options::default().bail_if_untrusted(true),
    )
    .map_err(|error| {
        format!(
            "cannot open Git repository {}: {error}",
            repository.display()
        )
    })
}

fn object_id(hex: &str) -> Result<gix::ObjectId, String> {
    gix::ObjectId::from_hex(hex.as_bytes())
        .map_err(|error| format!("invalid object ID {hex}: {error}"))
}

/// A repository-relative path that stays inside the snapshot.
fn safe_path(path: &str) -> Result<String, String> {
    let unsafe_part = Path::new(path)
        .components()
        .any(|component| !matches!(component, Component::Normal(_)));
    if path.is_empty() || unsafe_part {
        return Err(format!("unsafe path in Git snapshot: {path}"));
    }
    Ok(path.to_owned())
}

/// `path` with its longest existing ancestor resolved, so a file the working
/// tree lacks still compares with a resolved repository path.
fn canonical(path: &Path) -> PathBuf {
    for ancestor in path.ancestors() {
        if let Ok(resolved) = fs::canonicalize(ancestor) {
            let rest = path.strip_prefix(ancestor).unwrap_or(Path::new(""));
            return if rest.as_os_str().is_empty() {
                resolved
            } else {
                resolved.join(rest)
            };
        }
    }
    path.to_path_buf()
}
