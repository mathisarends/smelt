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
use std::sync::{Arc, LazyLock, PoisonError, RwLock};

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

/// Real directory listings that every walk of one check shares.
///
/// Discovery, coverage and YAML naming walk overlapping trees, and on Windows a
/// listing costs far more than reading it again from memory. A check enables
/// the cache while it runs and fills it in parallel with `prefetch`; walks then
/// list in the same order as before, so their results do not change.
struct Listings {
    /// The checks that currently share the cache.
    users: usize,
    directories: HashMap<PathBuf, Arc<Vec<Entry>>>,
}

static LISTINGS: LazyLock<RwLock<Listings>> = LazyLock::new(|| {
    RwLock::new(Listings {
        users: 0,
        directories: HashMap::new(),
    })
});

/// Shares real directory listings while this value lives.
#[must_use = "listings are shared only while the value lives"]
pub struct ListingCache(());

impl ListingCache {
    pub fn enable() -> Self {
        LISTINGS
            .write()
            .unwrap_or_else(PoisonError::into_inner)
            .users += 1;
        Self(())
    }
}

impl Drop for ListingCache {
    fn drop(&mut self) {
        let mut listings = LISTINGS.write().unwrap_or_else(PoisonError::into_inner);
        listings.users -= 1;
        if listings.users == 0 {
            listings.directories.clear();
        }
    }
}

/// List the real directories below `roots` in parallel into the enabled cache.
///
/// `descend` chooses the listed directories to visit; links are never followed,
/// so a cycle cannot recur, and the walks resolve them as before.
pub fn prefetch(roots: &[PathBuf], descend: impl Fn(&Entry) -> bool + Sync) {
    use rayon::prelude::*;
    let fresh = |directory: &PathBuf| {
        let listings = LISTINGS.read().unwrap_or_else(PoisonError::into_inner);
        listings.users > 0
            && !listings.directories.contains_key(directory)
            && mounted(directory).is_none()
    };
    let mut seen: BTreeSet<PathBuf> = BTreeSet::new();
    let mut level: Vec<PathBuf> = roots
        .iter()
        .filter(|directory| fresh(directory) && seen.insert((*directory).clone()))
        .cloned()
        .collect();
    let listed = |directory: PathBuf| list(&directory).ok().map(|entries| (directory, entries));
    while !level.is_empty() {
        // Outside an analysis pool, listing in parallel would start rayon's global pool.
        let listed: Vec<(PathBuf, Vec<Entry>)> = if rayon::current_thread_index().is_some() {
            level.into_par_iter().filter_map(listed).collect()
        } else {
            level.into_iter().filter_map(listed).collect()
        };
        level = listed
            .iter()
            .flat_map(|(_, entries)| entries)
            .filter(|entry| entry.kind == Kind::Directory && descend(entry))
            .filter(|entry| fresh(&entry.path) && seen.insert(entry.path.clone()))
            .map(|entry| entry.path.clone())
            .collect();
        let mut listings = LISTINGS.write().unwrap_or_else(PoisonError::into_inner);
        if listings.users == 0 {
            return;
        }
        for (directory, entries) in listed {
            listings
                .directories
                .entry(directory)
                .or_insert_with(|| Arc::new(entries));
        }
    }
}

/// One listed directory entry and its kind as the listing reports it.
#[derive(Clone)]
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
    {
        let listings = LISTINGS.read().unwrap_or_else(PoisonError::into_inner);
        if let Some(entries) = listings.directories.get(directory) {
            return Ok(entries.as_ref().clone());
        }
    }
    list(directory)
}

/// The entries of a real directory, in the order the filesystem lists them.
fn list(directory: &Path) -> io::Result<Vec<Entry>> {
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
    use super::{
        ListingCache, Mount, Overlay, canonicalize, exists, is_dir, is_file, prefetch, read,
        read_dir,
    };
    use std::collections::BTreeMap;
    use std::fs;
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

    #[test]
    fn a_listing_cache_serves_prefetched_directories_until_its_last_user_drops() {
        let root = std::env::temp_dir().join(format!("smelt-listing-test-{}", std::process::id()));
        let package = root.join("app");
        fs::create_dir_all(&package).unwrap();
        fs::write(package.join("core.py"), "").unwrap();
        let names = |directory: &PathBuf| -> Vec<PathBuf> {
            read_dir(directory)
                .unwrap()
                .into_iter()
                .map(|entry| entry.path)
                .collect()
        };

        let outer = ListingCache::enable();
        let inner = ListingCache::enable();
        prefetch(std::slice::from_ref(&root), |_| true);
        fs::write(package.join("added.py"), "").unwrap();
        let cached = names(&package);
        drop(inner);
        let still_cached = names(&package);
        drop(outer);
        let fresh = names(&package);
        fs::remove_dir_all(&root).unwrap();

        assert_eq!(cached, [package.join("core.py")]);
        assert_eq!(still_cached, cached);
        assert_eq!(fresh, [package.join("added.py"), package.join("core.py")]);
    }
}
