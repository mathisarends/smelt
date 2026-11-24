//! What a test path mirrors under `tests.mirror`, like `smelt.rules.testing.location`.
use crate::checker::config::TestPolicy;
use crate::checker::files::FileIndex;
use regex::Regex;

/// What a test path says it mirrors: `billing/test_invoice.py` -> billing, invoice.
pub struct Mirror {
    pub directories: Vec<String>,
    pub module: String,
    pub root: Option<String>,
}

/// The mirror convention of one project.
pub struct Mirroring<'a> {
    pub files: &'a FileIndex,
    pub policy: &'a TestPolicy,
    pub root_packages: &'a [String],
    pattern: Regex,
}

impl<'a> Mirroring<'a> {
    pub fn new(files: &'a FileIndex, policy: &'a TestPolicy, root_packages: &'a [String]) -> Self {
        Self {
            files,
            policy,
            root_packages,
            pattern: mirror_regex(&policy.mirror),
        }
    }

    /// The modules a discovered test's path mirrors, one per candidate root package.
    ///
    /// `conftest.py` and paths outside `tests.mirror` mirror nothing.
    pub fn mirrored_modules(&self, path: &str) -> Vec<String> {
        let Some(test_root) = self.files.test_root(path) else {
            return Vec::new();
        };
        if is_conftest(path) {
            return Vec::new();
        }
        let Some(mirror) = self.parts(path, test_root) else {
            return Vec::new();
        };
        self.roots(&mirror, test_root)
            .into_iter()
            .map(|root| module_of(&root, &mirror))
            .collect()
    }

    pub fn parts(&self, path: &str, test_root: &str) -> Option<Mirror> {
        let prefix = format!("{test_root}/");
        let relative = path.strip_prefix(&prefix).unwrap_or(path);
        let found = self.pattern.captures(relative)?;
        let path = found
            .name("path")
            .map(|item| item.as_str())
            .filter(|item| !item.is_empty());
        Some(Mirror {
            directories: path
                .map(|path| path.split('/').map(str::to_owned).collect())
                .unwrap_or_default(),
            module: found
                .name("module")
                .map_or("", |item| item.as_str())
                .to_owned(),
            root: found.name("root").map(|item| item.as_str().to_owned()),
        })
    }

    /// The root packages a test root belongs to: in a workspace, the source roots
    /// sharing the longest leading path with the test root.
    pub fn member_roots(&self, test_root: &str) -> Vec<String> {
        let shared: Vec<usize> = self
            .root_packages
            .iter()
            .map(|root| self.common_depth(root, test_root))
            .collect();
        let best = shared.iter().copied().max().unwrap_or(0);
        if best == 0 {
            return self.root_packages.to_vec();
        }
        self.root_packages
            .iter()
            .zip(shared)
            .filter(|(_, depth)| *depth == best)
            .map(|(root, _)| root.clone())
            .collect()
    }

    fn common_depth(&self, root: &str, test_root: &str) -> usize {
        let Some(source_root) = self.files.package_source_root(root) else {
            return 0;
        };
        source_root
            .trim_matches('/')
            .split('/')
            .zip(test_root.split('/'))
            .take_while(|(left, right)| left == right && !matches!(*left, "" | "."))
            .count()
    }

    pub fn roots(&self, mirror: &Mirror, test_root: &str) -> Vec<String> {
        let roots = self.member_roots(test_root);
        match &mirror.root {
            None => roots,
            Some(root) if roots.contains(root) => vec![root.clone()],
            Some(_) => Vec::new(),
        }
    }

    /// Where `tests.mirror` puts the test of `module` (a module, not a package).
    pub fn target(&self, test_root: &str, module: &str) -> String {
        let parts: Vec<&str> = module.split('.').collect();
        let (root, rest) = parts.split_first().unwrap_or((&"", &[]));
        let (name, directories) = rest.split_last().unwrap_or((&"", &[]));
        let path = directories.join("/");
        let relative = self
            .policy
            .mirror
            .replace(
                "{path}/",
                &if path.is_empty() {
                    String::new()
                } else {
                    format!("{path}/")
                },
            )
            .replace("{path}", &path)
            .replace("{module}", name)
            .replace("{root}", root);
        format!("{test_root}/{relative}")
    }
}

pub fn module_of(root: &str, mirror: &Mirror) -> String {
    std::iter::once(root)
        .chain(mirror.directories.iter().map(String::as_str))
        .chain(std::iter::once(mirror.module.as_str()))
        .collect::<Vec<_>>()
        .join(".")
}

pub fn is_conftest(path: &str) -> bool {
    path.rsplit('/').next() == Some("conftest.py")
}

fn mirror_regex(pattern: &str) -> Regex {
    let placeholder =
        Regex::new(r"(\{path\}/|\{path\}|\{module\}|\{root\})").expect("valid pattern");
    let mut built = String::from("^(?:");
    let mut last = 0;
    for found in placeholder.find_iter(pattern) {
        built.push_str(&regex::escape(&pattern[last..found.start()]));
        built.push_str(match found.as_str() {
            "{path}/" => r"(?:(?P<path>[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)/)?",
            "{path}" => r"(?P<path>[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)?",
            "{module}" => r"(?P<module>[A-Za-z_]\w*)",
            _ => r"(?P<root>[A-Za-z_]\w*)",
        });
        last = found.end();
    }
    built.push_str(&regex::escape(&pattern[last..]));
    built.push_str(")$");
    Regex::new(&built).expect("tests.mirror is validated on load")
}
