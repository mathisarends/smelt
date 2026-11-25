//! File reads that see mounted Git snapshots, else the real filesystem.
//!
//! A snapshot of the staged index or a base revision lives in memory below a
//! path that does not exist on disk. Every read the core makes of a project
//! goes through here, so a check of a snapshot reads it like a working tree.
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, PoisonError, RwLock};

static MOUNTS: RwLock<Vec<Arc<Overlay>>> = RwLock::new(Vec::new());

/// Files and directories below one root that exists only in memory.
pub struct Overlay {
    root: PathBuf,
    files: HashMap<PathBuf, Vec<u8>>,
    /// Every directory, the root included, with its children's names.
    directories: HashMap<PathBuf, BTreeSet<(OsString, bool)>>,
}

impl Overlay {
    /// The directory `top` below `root`, holding `files` by `/`-separated path.
    pub fn new(root: PathBuf, top: &str, files: BTreeMap<String, Vec<u8>>) -> Self {
        let mut directories: HashMap<PathBuf, BTreeSet<(OsString, bool)>> = HashMap::from([
            (
                PathBuf::new(),
                BTreeSet::from([(OsString::from(top), true)]),
            ),
            (PathBuf::from(top), BTreeSet::new()),
        ]);
        let mut contents = HashMap::with_capacity(files.len());
        for (path, content) in files {
            let relative: PathBuf = std::iter::once(top).chain(path.split('/')).collect();
            let mut is_dir = false;
            let mut child = relative.as_path();
            while let Some(parent) = child.parent() {
                let name = child.file_name().unwrap_or_default().to_owned();
                let siblings = directories.entry(parent.to_path_buf()).or_default();
                let known = siblings.contains(&(name.clone(), is_dir));
                siblings.insert((name, is_dir));
                if known {
                    break;
                }
                is_dir = true;
                child = parent;
            }
            contents.insert(relative, content);
        }
        Self {
            root,
            files: contents,
            directories,
        }
    }
}

/// An overlay readable while this value lives.
pub struct Mount {
    overlay: Arc<Overlay>,
}

impl Mount {
    pub fn new(overlay: Overlay) -> Self {
        let overlay = Arc::new(overlay);
        MOUNTS
            .write()
            .unwrap_or_else(PoisonError::into_inner)
            .push(Arc::clone(&overlay));
        Self { overlay }
    }
}

impl Drop for Mount {
    fn drop(&mut self) {
        MOUNTS
            .write()
            .unwrap_or_else(PoisonError::into_inner)
            .retain(|mounted| !Arc::ptr_eq(mounted, &self.overlay));
    }
}

/// One listed directory entry and its kind as the listing reports it.
pub struct Entry {
    pub path: PathBuf,
    kind: Kind,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Directory,
    File,
    /// A symbolic link, or an entry whose kind the listing did not tell.
    Link,
}

impl Entry {
    /// A symbolic link, which only the real filesystem has.
    pub fn is_link(&self) -> bool {
        self.kind == Kind::Link
    }

    /// Whether this is a directory, following links as `Path::is_dir` does.
    pub fn is_dir(&self) -> bool {
        match self.kind {
            Kind::Directory => true,
            Kind::File => false,
            Kind::Link => is_dir(&self.path),
        }
    }

    /// Whether this is a file, following links as `Path::is_file` does.
    pub fn is_file(&self) -> bool {
        match self.kind {
            Kind::Directory => false,
            Kind::File => true,
            Kind::Link => is_file(&self.path),
        }
    }
}

/// The mounted overlay holding `path`, and `path` relative to its root.
fn mounted(path: &Path) -> Option<(Arc<Overlay>, PathBuf)> {
    let mounts = MOUNTS.read().unwrap_or_else(PoisonError::into_inner);
    mounts.iter().find_map(|overlay| {
        let rest = path.strip_prefix(&overlay.root).ok()?;
        let mut relative = PathBuf::new();
        for component in rest.components() {
            match component {
                Component::Normal(part) => relative.push(part),
                Component::ParentDir if !relative.pop() => {
                    return None;
                }
                _ => {}
            }
        }
        Some((Arc::clone(overlay), relative))
    })
}

fn not_found(path: &Path) -> io::Error {
    io::Error::new(
        io::ErrorKind::NotFound,
        format!("{} is not in the snapshot", path.display()),
    )
}

pub fn read(path: &Path) -> io::Result<Vec<u8>> {
    match mounted(path) {
        Some((overlay, relative)) => overlay
            .files
            .get(&relative)
            .cloned()
            .ok_or_else(|| not_found(path)),
        None => fs::read(path),
    }
}

pub fn read_to_string(path: &Path) -> io::Result<String> {
    String::from_utf8(read(path)?)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

/// The entries of `directory`, sorted by name in a snapshot.
pub fn read_dir(directory: &Path) -> io::Result<Vec<Entry>> {
    if let Some((overlay, relative)) = mounted(directory) {
        let children = overlay
            .directories
            .get(&relative)
            .ok_or_else(|| not_found(directory))?;
        return Ok(children
            .iter()
            .map(|(name, is_dir)| Entry {
                path: directory.join(name),
                kind: if *is_dir { Kind::Directory } else { Kind::File },
            })
            .collect());
    }
    Ok(fs::read_dir(directory)?
        .flatten()
        .map(|entry| {
            let kind = match entry.file_type() {
                Ok(kind) if kind.is_dir() => Kind::Directory,
                Ok(kind) if kind.is_file() => Kind::File,
                _ => Kind::Link,
            };
            Entry {
                path: entry.path(),
                kind,
            }
        })
        .collect())
}

/// `fs::canonicalize`; a snapshot path resolves lexically, as it has no links.
pub fn canonicalize(path: &Path) -> io::Result<PathBuf> {
    match mounted(path) {
        Some((overlay, relative)) if exists_in(&overlay, &relative) => {
            Ok(overlay.root.join(relative))
        }
        Some(_) => Err(not_found(path)),
        None => fs::canonicalize(path),
    }
}

pub fn is_dir(path: &Path) -> bool {
    match mounted(path) {
        Some((overlay, relative)) => overlay.directories.contains_key(&relative),
        None => path.is_dir(),
    }
}

pub fn is_file(path: &Path) -> bool {
    match mounted(path) {
        Some((overlay, relative)) => overlay.files.contains_key(&relative),
        None => path.is_file(),
    }
}

pub fn exists(path: &Path) -> bool {
    match mounted(path) {
        Some((overlay, relative)) => exists_in(&overlay, &relative),
        None => path.exists(),
    }
}

/// A directory that is not a symbolic link, like `symlink_metadata().is_dir()`.
pub fn is_real_dir(path: &Path) -> bool {
    match mounted(path) {
        Some((overlay, relative)) => overlay.directories.contains_key(&relative),
        None => fs::symlink_metadata(path).is_ok_and(|metadata| metadata.is_dir()),
    }
}

fn exists_in(overlay: &Overlay, relative: &Path) -> bool {
    overlay.files.contains_key(relative) || overlay.directories.contains_key(relative)
}

#[cfg(test)]
mod tests {
    use super::{Mount, Overlay, canonicalize, exists, is_dir, is_file, read, read_dir};
    use std::collections::BTreeMap;
    use std::path::PathBuf;

    #[test]
    fn a_mounted_overlay_reads_like_a_directory_tree() {
        let root = std::env::temp_dir().join("smelt-vfs-test-never-created");
        let files = BTreeMap::from([
            ("app/__init__.py".to_owned(), b"".to_vec()),
            ("app/core.py".to_owned(), b"VALUE = 1\n".to_vec()),
            ("README.md".to_owned(), Vec::new()),
        ]);
        let mount = Mount::new(Overlay::new(root.clone(), "repository", files));
        let tree = root.join("repository");

        assert!(is_dir(&root) && is_dir(&tree) && is_dir(&tree.join("app")));
        assert!(is_file(&tree.join("app/core.py")) && !is_dir(&tree.join("app/core.py")));
        assert_eq!(
            read(&tree.join("app/../app/core.py")).unwrap(),
            b"VALUE = 1\n"
        );
        assert!(!exists(&tree.join("missing.py")));
        assert_eq!(
            canonicalize(&tree.join("app/./core.py")).unwrap(),
            tree.join("app/core.py")
        );
        let names: Vec<PathBuf> = read_dir(&tree)
            .unwrap()
            .into_iter()
            .map(|entry| entry.path)
            .collect();
        assert_eq!(names, [tree.join("README.md"), tree.join("app")]);

        drop(mount);
        assert!(!is_dir(&tree));
    }
}
