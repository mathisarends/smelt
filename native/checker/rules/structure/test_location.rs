//! TST001: test files that do not live where `tests.layout` expects them.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::bindings::import_bindings;
use crate::checker::analysis::mirror::{Mirror, Mirroring, is_conftest, module_of};
use crate::checker::analysis::namespace::{Namespace, project_namespaces, resolve_dotted};
use crate::checker::analysis::similarity::{close_matches, ratio};
use crate::checker::patterns::{PathPattern, is_within};
use crate::checker::source::ParsedSource;
use serde_json::{Map, Value, json};
use std::collections::{BTreeSet, HashMap, HashSet};

const NO_SOURCE: &str =
    "A path without a source module does not mean the tested behaviour is missing.";
// Enough to choose from; the JSON says how many there are.
const MAX_CANDIDATES: usize = 5;
// Typos score above 0.85 (invoise/invoice); spotify_service/spotify_search scores
// 0.83 and is another module, not a misspelling.
const TYPO_CUTOFF: f64 = 0.85;

fn generic_hint() -> String {
    format!(
        "{NO_SOURCE} Move or rename the test to mirror the module it covers, merge it into that module's test, delete it if the module is gone, or list a deliberate behaviour or integration test in tests.mirror_exclude."
    )
}

/// A first-party name a test imports and the module that defines it.
#[derive(PartialEq)]
struct Import {
    /// The name, or the module for `import` and `from . import x`.
    name: String,
    module: String,
    /// The package facade that re-exports it.
    via: Option<String>,
}

struct Location<'c, 'a> {
    context: &'c StructureContext<'a>,
    mirroring: Mirroring<'a>,
    namespaces: HashMap<&'a str, Namespace>,
    packages: HashSet<&'a str>,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let policy = context.test_policy;
    if policy.layout == "none" {
        return Vec::new();
    }
    let Ok(excluded) = policy
        .mirror_exclude
        .iter()
        .map(|pattern| PathPattern::new(pattern))
        .collect::<Result<Vec<_>, _>>()
    else {
        return Vec::new();
    };
    let location = Location {
        context,
        mirroring: Mirroring::new(context.files, policy, &context.project.root_packages),
        namespaces: project_namespaces(context.sources, &context.files.packages),
        packages: context.files.packages.iter().map(String::as_str).collect(),
    };
    let parsed: HashMap<&str, &ParsedSource> = context
        .tests
        .iter()
        .map(|(test, parsed)| (test.path.as_str(), *parsed))
        .collect();
    let mut found = Vec::new();
    for (path, test) in &context.files.tests {
        if is_conftest(path) || excluded.iter().any(|pattern| pattern.matches(path)) {
            continue;
        }
        let Some(test_root) = context.files.test_root(path) else {
            continue;
        };
        let imports = parsed
            .get(path.as_str())
            .map(|parsed| location.resolved_imports(parsed, &test.module))
            .unwrap_or_default();
        found.extend(location.check_mirror(path, test_root, &imports));
    }
    found
}

fn name_of(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

/// `test_invoice.py` -> `invoice`.
fn subject_of(name: &str) -> &str {
    let stem = name.strip_suffix(".py").unwrap_or(name);
    let stem = stem.strip_prefix("test_").unwrap_or(stem);
    stem.strip_suffix("_test").unwrap_or(stem)
}

fn parent(module: &str) -> &str {
    module.rsplit_once('.').map_or("", |(parent, _)| parent)
}

fn last(module: &str) -> &str {
    module.rsplit('.').next().unwrap_or(module)
}

fn dirname(path: &str) -> &str {
    path.rsplit_once('/').map_or("", |(directory, _)| directory)
}

/// `session_infrastructure_repository` names `session.infrastructure.repository`.
///
/// The prefix must be the end of the module's package path.
fn spells_package_path(subject: &str, module: &str) -> bool {
    let parts: Vec<&str> = module.split('.').collect();
    let Some((name, package)) = parts.split_last() else {
        return false;
    };
    let Some(prefix) = subject.strip_suffix(&format!("_{name}")) else {
        return false;
    };
    (0..package.len()).any(|start| prefix == package[start..].join("_"))
}

/// The module names a test may cover: `invoice_rounding` -> also `invoice`.
fn subjects(subject: &str, suffixes: bool) -> Vec<String> {
    if !suffixes {
        return vec![subject.to_owned()];
    }
    let parts: Vec<&str> = subject.split('_').collect();
    (1..=parts.len())
        .rev()
        .map(|end| parts[..end].join("_"))
        .collect()
}

fn unique_modules(imports: &[Import]) -> Vec<String> {
    let mut seen = HashSet::new();
    imports
        .iter()
        .filter(|item| seen.insert(item.module.as_str()))
        .map(|item| item.module.clone())
        .collect()
}

impl<'a> Location<'_, 'a> {
    fn files(&self) -> &'a crate::checker::files::FileIndex {
        self.context.files
    }

    fn is_package(&self, module: &str) -> bool {
        self.packages.contains(module)
    }

    /// First-party imports, followed through package facades to their modules.
    fn resolved_imports(&self, parsed: &ParsedSource, module: &str) -> Vec<Import> {
        let mut found: Vec<Import> = Vec::new();
        let bindings = import_bindings(parsed.module(), module, false);
        let mut targets: Vec<&String> = bindings.values().collect();
        targets.sort();
        for target in targets {
            let Some(module) = self.first_party_module(target) else {
                continue;
            };
            let mut item = Import {
                name: target.clone(),
                module: module.to_owned(),
                via: None,
            };
            if target != module {
                let name = target[module.len() + 1..].to_owned();
                item = match resolve_dotted(target, &self.namespaces) {
                    Some((defining, _)) if defining != module => Import {
                        name,
                        module: defining,
                        via: Some(module.to_owned()),
                    },
                    _ => Import {
                        name,
                        module: module.to_owned(),
                        via: None,
                    },
                };
            }
            if !found.contains(&item) {
                found.push(item);
            }
        }
        found
    }

    /// The longest source module that prefixes `qualname`.
    fn first_party_module<'q>(&self, qualname: &'q str) -> Option<&'q str> {
        let mut end = qualname.len();
        loop {
            let candidate = &qualname[..end];
            if self.files().sources.contains_key(candidate) {
                return Some(candidate);
            }
            end = candidate.rfind('.')?;
        }
    }

    fn mirrors_source(&self, mirror: &Mirror, test_root: &str) -> bool {
        for root in self.mirroring.roots(mirror, test_root) {
            let package = std::iter::once(root.as_str())
                .chain(mirror.directories.iter().map(String::as_str))
                .collect::<Vec<_>>()
                .join(".");
            let package_name = mirror.directories.last().unwrap_or(&root);
            for name in subjects(&mirror.module, self.context.test_policy.mirror_suffixes) {
                let source = self.files().sources.get(&format!("{package}.{name}"));
                if source.is_some_and(|source| !source.is_package) {
                    return true;
                }
                if &name == package_name && self.is_package(&package) {
                    return true;
                }
            }
        }
        false
    }

    /// The imported modules the test is named after; several make it ambiguous.
    fn named_modules(&self, subject: &str, modules: &[String]) -> Vec<String> {
        let plain: Vec<&String> = modules
            .iter()
            .filter(|module| !self.is_package(module))
            .collect();
        let exact: Vec<String> = plain
            .iter()
            .filter(|module| last(module) == subject)
            .map(|module| (*module).clone())
            .collect();
        if !exact.is_empty() {
            return exact;
        }
        let suffixed: Vec<String> = plain
            .iter()
            .filter(|module| spells_package_path(subject, module))
            .map(|module| (*module).clone())
            .collect();
        if suffixed.len() == 1 {
            suffixed
        } else {
            Vec::new()
        }
    }

    /// The module next to the mirrored path that the name ends with.
    fn suffix_sibling(
        &self,
        mirror: &Mirror,
        test_root: &str,
        imported: &[String],
    ) -> Option<String> {
        let mut found = Vec::new();
        for root in self.mirroring.roots(mirror, test_root) {
            let package = std::iter::once(root.as_str())
                .chain(mirror.directories.iter().map(String::as_str))
                .collect::<Vec<_>>()
                .join(".");
            for source in self.files().sources.values() {
                if !source.is_package
                    && parent(&source.module) == package
                    && spells_package_path(&mirror.module, &source.module)
                {
                    found.push(source.module.clone());
                }
            }
        }
        if found.len() != 1 {
            return None;
        }
        let tested = found.remove(0);
        let package = parent(&tested);
        let others = imported
            .iter()
            .any(|module| is_within(module, package) && module != package && *module != tested);
        (!others).then_some(tested)
    }

    /// The package the test is named after, next to the mirrored path.
    fn named_package(
        &self,
        mirror: &Mirror,
        test_root: &str,
        imported: &[String],
    ) -> Option<String> {
        let found: Vec<String> = self
            .mirroring
            .roots(mirror, test_root)
            .iter()
            .map(|root| module_of(root, mirror))
            .filter(|package| self.is_package(package))
            .collect();
        let [package] = found.as_slice() else {
            return None;
        };
        if !imported.is_empty() && !imported.iter().any(|module| is_within(module, package)) {
            return None;
        }
        Some(package.clone())
    }

    fn suffix_hint(&self) -> &'static str {
        if self.context.test_policy.mirror_suffixes {
            " Topic tests may add a suffix: test_<module>_<topic>.py."
        } else {
            " tests.mirror_suffixes allows topic tests like test_<module>_<topic>.py."
        }
    }

    /// A hint when the test path starts with the root package the pattern leaves out.
    fn root_pattern_hint(&self, mirror: &Mirror, test_root: &str) -> String {
        let pattern = &self.context.test_policy.mirror;
        if pattern.contains("{root}") {
            return String::new();
        }
        let Some((root, directories)) = mirror.directories.split_first() else {
            return String::new();
        };
        if !self.mirroring.member_roots(test_root).contains(root) {
            return String::new();
        }
        let shifted = Mirror {
            directories: directories.to_vec(),
            module: mirror.module.clone(),
            root: Some(root.clone()),
        };
        if !self.mirrors_source(&shifted, test_root) {
            return String::new();
        }
        format!(
            " The test path starts with the root package \"{root}\"; if all tests do, set tests.mirror to \"{{root}}/{pattern}\"."
        )
    }

    fn missing_module(&self, mirror: &Mirror, test_root: &str) -> Option<String> {
        let mut roots = self.mirroring.roots(mirror, test_root);
        if roots.is_empty() {
            roots = self.mirroring.member_roots(test_root);
        }
        roots.first().map(|root| module_of(root, mirror))
    }

    /// ` (did you mean "invoice.py"?)` for a typo next to a module the test imports.
    fn similar_module(&self, module: &str, imported: &[String]) -> String {
        let (package, name) = module.rsplit_once('.').unwrap_or(("", module));
        let stems: Vec<&str> = self
            .files()
            .source_order
            .iter()
            .filter_map(|sibling| self.files().sources.get(sibling))
            .filter(|source| {
                !source.is_package
                    && parent(&source.module) == package
                    && imported.contains(&source.module)
            })
            .map(|source| last(&source.module))
            .collect();
        match close_matches(name, &stems, 1, TYPO_CUTOFF).first() {
            Some(found) => format!(" (did you mean \"{found}.py\"?)"),
            None => String::new(),
        }
    }

    /// Imported modules of the test's own member, nearest to its mirrored path first.
    fn ranked(&self, test_root: &str, missing: &str, modules: &[String]) -> Vec<String> {
        let roots = self.mirroring.member_roots(test_root);
        let (package, name) = missing.rsplit_once('.').unwrap_or(("", missing));
        let mut ranked: Vec<(bool, f64, &String)> = modules
            .iter()
            .filter(|module| {
                roots
                    .iter()
                    .any(|root| module.split('.').next() == Some(root.as_str()))
            })
            .map(|module| {
                (
                    !is_within(module, package),
                    ratio(name, last(module)),
                    module,
                )
            })
            .collect();
        ranked.sort_by(|left, right| {
            left.0
                .cmp(&right.0)
                .then_with(|| right.1.total_cmp(&left.1))
                .then_with(|| left.2.cmp(right.2))
        });
        ranked
            .into_iter()
            .map(|(_, _, module)| module.clone())
            .collect()
    }

    /// The first ranked modules with what the test imports from them.
    fn candidates(&self, test_root: &str, ranked: &[String], imports: &[Import]) -> Vec<Value> {
        ranked
            .iter()
            .take(MAX_CANDIDATES)
            .map(|module| {
                let is_package = self.is_package(module);
                let target = if is_package {
                    format!("{module}.{}", last(module))
                } else {
                    module.clone()
                };
                let names: BTreeSet<&str> = imports
                    .iter()
                    .filter(|item| &item.module == module)
                    .map(|item| item.name.as_str())
                    .collect();
                let via: BTreeSet<&str> = imports
                    .iter()
                    .filter(|item| &item.module == module)
                    .filter_map(|item| item.via.as_deref())
                    .collect();
                json!({
                    "module": module,
                    "kind": if is_package { "package" } else { "module" },
                    "source": self.files().module_to_path(module, is_package),
                    "test_path": self.mirroring.target(test_root, &target),
                    "imports": names,
                    "via": via,
                })
            })
            .collect()
    }

    fn listing(&self, modules: &[String]) -> String {
        let shown: Vec<String> = modules
            .iter()
            .take(3)
            .map(|module| {
                self.context
                    .model
                    .display_path(module, self.is_package(module))
            })
            .collect();
        let more = modules.len() - shown.len();
        let mut text = shown.join(", ");
        if more > 0 {
            text.push_str(&format!(" and {more} more"));
        }
        text
    }

    fn located(&self, module: &str, finding: StructureFinding) -> StructureFinding {
        let info = self.context.model.info(module);
        StructureFinding {
            feature: info.as_ref().and_then(|info| info.feature.clone()),
            layer: info.as_ref().and_then(|info| info.layer.clone()),
            ..finding
        }
    }

    fn check_mirror(
        &self,
        path: &str,
        test_root: &str,
        imports: &[Import],
    ) -> Option<StructureFinding> {
        let mirror = self.mirroring.parts(path, test_root);
        if mirror
            .as_ref()
            .is_some_and(|mirror| self.mirrors_source(mirror, test_root))
        {
            return None;
        }
        let name = name_of(path);
        let modules = unique_modules(imports);
        let subject = mirror
            .as_ref()
            .map_or_else(|| subject_of(name), |mirror| mirror.module.as_str());
        let named = self.named_modules(subject, &modules);
        let mut tested = (named.len() == 1).then(|| named[0].clone());
        if let (true, Some(mirror)) = (named.is_empty(), &mirror) {
            tested = self.suffix_sibling(mirror, test_root, &modules);
        }
        let mut package = None;
        if named.is_empty()
            && tested.is_none()
            && let Some(mirror) = &mirror
        {
            package = self.named_package(mirror, test_root, &modules);
        }
        let mut taken = String::new();
        if let Some(covered) = tested.as_ref().or(package.as_ref()) {
            // A package's own test sits inside it: billing/stripe/test_stripe.py
            let target = if tested.is_some() {
                covered.clone()
            } else {
                format!("{covered}.{subject}")
            };
            let expected = self.mirroring.target(test_root, &target);
            if self.files().tests.contains_key(&expected) && expected != path {
                taken = format!(" {expected} already exists; merge the two tests.");
            } else if expected != path {
                return Some(self.misplaced(path, covered, &expected, &modules));
            }
        }
        let Some(mirror) = &mirror else {
            let pattern = &self.context.test_policy.mirror;
            return Some(StructureFinding {
                message: format!("{name} does not match tests.mirror ({pattern})"),
                path: path.to_owned(),
                expected: json!({"pattern": format!("{test_root}/{pattern}")}),
                hint: format!("{}{}{taken}", generic_hint(), self.suffix_hint()),
                ..StructureFinding::default()
            });
        };
        let module = self.missing_module(mirror, test_root)?;
        let source = self.files().module_to_path(&module, false);
        let ranked = self.ranked(
            test_root,
            &module,
            if named.is_empty() { &modules } else { &named },
        );
        let candidates = self.candidates(test_root, &ranked, imports);
        let mut details = Map::new();
        details.insert("source".to_owned(), json!(source));
        let mut message = format!("{name} has no source module at its mirrored path {source}");
        if named.len() <= 1 {
            message += &self.similar_module(&module, &modules);
        }
        let hint = if named.len() > 1 {
            message += &format!("; it imports {} modules named {subject}", named.len());
            details.insert("subject".to_owned(), json!("ambiguous"));
            details.insert("candidates".to_owned(), json!(candidates));
            format!(
                "It imports {}. Move it next to the one it tests, split it, or list it in tests.mirror_exclude if it tests how they work together.",
                self.listing(&ranked)
            )
        } else if !candidates.is_empty() {
            details.insert("subject".to_owned(), json!("unknown"));
            details.insert("candidates".to_owned(), json!(candidates));
            format!(
                "{NO_SOURCE} It imports {}: rename or move it to the test of the module it covers, merge it into that test, or list it in tests.mirror_exclude if it tests behaviour across them.",
                self.listing(&ranked)
            )
        } else {
            generic_hint()
        };
        let finding = StructureFinding {
            message,
            path: path.to_owned(),
            expected: Value::Object(details),
            hint: format!(
                "{hint}{}{taken}{}",
                self.suffix_hint(),
                self.root_pattern_hint(mirror, test_root)
            ),
            ..StructureFinding::default()
        };
        Some(self.located(&module, finding))
    }

    fn misplaced(
        &self,
        path: &str,
        module: &str,
        expected: &str,
        imported: &[String],
    ) -> StructureFinding {
        let name = name_of(path);
        let directory = dirname(expected);
        let message = if directory == dirname(path) {
            format!("{name} should be named {}", name_of(expected))
        } else {
            format!("{name} belongs in {directory}/")
        };
        let package = self.is_package(module);
        let source = self.files().module_to_path(module, package);
        let (evidence, hint) = if imported.iter().any(|item| is_within(item, module)) {
            ("imports", format!("Move the file to {expected}."))
        } else {
            (
                "name",
                format!(
                    "Move the file to {expected}. Only its name points there: it imports no code of {source}, so check that it really tests it."
                ),
            )
        };
        let finding = StructureFinding {
            message,
            path: path.to_owned(),
            expected: json!({
                "path": expected,
                "source": source,
                "subject": if package { "package" } else { "module" },
                "evidence": evidence,
            }),
            hint,
            ..StructureFinding::default()
        };
        self.located(module, finding)
    }
}
