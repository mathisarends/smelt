//! The strict-coverage inventory, independent of package selection and imports.
use super::architecture::{ArchitectureModel, ModuleInfo, ModuleKind};
use super::nodes::walk_statements;
use crate::checker::config::discovery::import_root;
use crate::checker::config::paths;
use crate::checker::config::workspace::member_directories;
use crate::checker::config::{Exclusion, Project};
use crate::checker::files::{FileIndex, SourceFile, is_identifier, python_files, relative_path};
use crate::checker::patterns::PathPattern;
use crate::checker::source::ParsedSource;
use ruff_python_ast::{
    ExceptHandler, Expr, ModModule, Number, Operator, Stmt, StmtAnnAssign, StmtAssign, UnaryOp,
};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

const UNCOVERED_REASON: &str =
    "Not selected by project.root_packages/source_roots, or not an importable module.";

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Governed,
    Facade,
    Unclassified,
    Uncovered,
    Excluded,
}

impl Status {
    pub fn name(self) -> &'static str {
        match self {
            Self::Governed => "governed",
            Self::Facade => "facade",
            Self::Unclassified => "unclassified",
            Self::Uncovered => "uncovered",
            Self::Excluded => "excluded",
        }
    }
}

pub struct CoverageEntry {
    pub path: String,
    pub module: Option<String>,
    pub status: Status,
    pub kind: Option<ModuleKind>,
    pub feature: Option<String>,
    pub layer: Option<String>,
    pub wiring: bool,
    pub central: bool,
    pub reason: Option<String>,
    /// The architecture settings that classify the module.
    pub policies: Vec<String>,
    pub source_root: Option<String>,
}

impl CoverageEntry {
    /// An inventoried file no architecture classifies.
    fn unclassified(path: String, module: Option<String>, status: Status) -> Self {
        Self {
            path,
            module,
            status,
            kind: None,
            feature: None,
            layer: None,
            wiring: false,
            central: false,
            reason: None,
            policies: Vec::new(),
            source_root: None,
        }
    }

    pub fn to_json(&self) -> Value {
        json!({
            "path": self.path,
            "module": self.module,
            "status": self.status.name(),
            "kind": self.kind.map(ModuleKind::value),
            "feature": self.feature,
            "layer": self.layer,
            "wiring": self.wiring,
            "central": self.central,
            "reason": self.reason,
            "policies": self.policies,
            "source_root": self.source_root,
        })
    }
}

pub struct Coverage {
    /// Every inventoried file, sorted by path.
    pub entries: Vec<CoverageEntry>,
    /// The directories searched for Python files, relative to the project.
    pub scan_roots: Vec<String>,
    /// The `project.exclude` patterns given without a reason.
    pub unexplained_exclusions: Vec<String>,
    /// Why workspace members could not be discovered, outside strict coverage.
    pub discovery_errors: Vec<String>,
}

/// What the inventory reads beyond the discovered files.
pub struct CoverageInputs<'a> {
    pub project: &'a Project,
    pub sources: &'a [(&'a SourceFile, &'a ParsedSource)],
    /// Strict coverage fails on a workspace it cannot discover.
    pub strict: bool,
}

impl Coverage {
    /// The coverage in `cell`, built on first use.
    ///
    /// ARC013 and the report of one check read the same inputs, so they share it.
    pub fn shared<'c>(
        cell: &'c OnceLock<Self>,
        files: &FileIndex,
        model: &ArchitectureModel<'_>,
        inputs: &CoverageInputs<'_>,
    ) -> Result<&'c Self, String> {
        if let Some(coverage) = cell.get() {
            return Ok(coverage);
        }
        let built = Self::build(files, model, inputs)?;
        Ok(cell.get_or_init(|| built))
    }

    pub fn build(
        files: &FileIndex,
        model: &ArchitectureModel<'_>,
        inputs: &CoverageInputs<'_>,
    ) -> Result<Self, String> {
        let project = inputs.project;
        let base = paths::resolve(&files.root);
        let mut roots: HashSet<PathBuf> = project
            .source_roots
            .iter()
            .map(|value| real(&base.join(value)))
            .collect();
        let mut test_roots: HashSet<PathBuf> = project
            .test_roots
            .iter()
            .map(|value| real(&base.join(value)))
            .collect();
        let mut discovery_errors = Vec::new();
        let members = match member_directories(&base, true) {
            Ok(members) => members,
            Err(error) if !inputs.strict => {
                discovery_errors.push(error);
                Vec::new()
            }
            Err(error) => return Err(error),
        };
        for member in members {
            let directory = base.join(&member.path);
            if !directory.is_dir() {
                continue;
            }
            // uv excludes control packaging, not architecture coverage.
            let located = import_root(&directory).unwrap_or_else(|| {
                let source = directory.join("src");
                if source.is_dir() {
                    source
                } else {
                    directory.clone()
                }
            });
            roots.insert(real(&located));
            test_roots.extend(["tests", "test"].map(|name| real(&directory.join(name))));
        }
        let mut inventory: BTreeMap<String, CoverageEntry> = inputs
            .sources
            .iter()
            .map(|(source, parsed)| {
                let entry = classify(source, parsed, files, model);
                (source.path.clone(), entry)
            })
            .collect();
        let exclusions = project
            .exclusions
            .iter()
            .map(|entry| Ok((PathPattern::new(&entry.path)?, entry)))
            .collect::<Result<Vec<(PathPattern, &Exclusion)>, String>>()?;
        let mut scan_roots: Vec<String> = roots
            .iter()
            .map(|root| match relative_path(&files.root, root) {
                relative if relative.is_empty() => ".".to_owned(),
                relative => relative,
            })
            .collect();
        scan_roots.sort();
        // The most specific import root names a module in nested workspaces.
        let mut ordered: Vec<PathBuf> = roots.into_iter().collect();
        ordered.sort_by_cached_key(|root| {
            (
                std::cmp::Reverse(root.components().count()),
                paths::posix(root),
            )
        });
        let mut tested: HashMap<PathBuf, bool> = HashMap::new();
        for directory in ordered {
            if !directory.is_dir() {
                continue;
            }
            for path in python_files(&directory) {
                let absolute = real(&path);
                let relative = relative_path(&files.root, &path);
                if inventory.contains_key(&relative) || test_roots.contains(&absolute) {
                    continue;
                }
                let parent = absolute.parent().unwrap_or(&absolute).to_path_buf();
                let under_test = *tested.entry(parent).or_insert_with_key(|parent| {
                    test_roots.iter().any(|root| parent.starts_with(root))
                });
                if under_test {
                    continue;
                }
                let module = module_name(&path, &directory);
                // `Path.relative_to` spells the root itself `.`.
                let source_root = Some(relative_path(&files.root, &directory)).map(|root| {
                    if root.is_empty() {
                        ".".to_owned()
                    } else {
                        root
                    }
                });
                let excluded = exclusions
                    .iter()
                    .find(|(pattern, _)| pattern.matches(&relative))
                    .map(|(_, entry)| *entry);
                let entry = match excluded {
                    Some(exclusion) => CoverageEntry {
                        reason: exclusion.reason.clone(),
                        source_root,
                        ..CoverageEntry::unclassified(relative.clone(), module, Status::Excluded)
                    },
                    None => CoverageEntry {
                        reason: Some(UNCOVERED_REASON.to_owned()),
                        source_root,
                        ..CoverageEntry::unclassified(relative.clone(), module, Status::Uncovered)
                    },
                };
                inventory.insert(relative, entry);
            }
        }
        Ok(Self {
            entries: inventory.into_values().collect(),
            scan_roots,
            unexplained_exclusions: project
                .exclusions
                .iter()
                .filter(|entry| entry.reason.is_none())
                .map(|entry| entry.path.clone())
                .collect(),
            discovery_errors,
        })
    }

    /// Whether every file is classified or excluded with a reason.
    pub fn complete(&self) -> bool {
        self.gaps().next().is_none()
            && self.unexplained_exclusions.is_empty()
            && self.discovery_errors.is_empty()
    }

    /// The inventory counts reports carry, as `CoverageIndex.summary`.
    pub fn summary(&self) -> Value {
        let count = |status: Status| {
            self.entries
                .iter()
                .filter(|entry| entry.status == status)
                .count()
        };
        json!({
            "complete": self.complete(),
            "counts": {
                "governed": count(Status::Governed),
                "facade": count(Status::Facade),
                "unclassified": count(Status::Unclassified),
                "uncovered": count(Status::Uncovered),
                "excluded": count(Status::Excluded),
            },
            "scan_roots": self.scan_roots,
            "unexplained_exclusions": self.unexplained_exclusions,
            "discovery_errors": self.discovery_errors,
        })
    }

    /// The unclassified and uncovered files.
    pub fn gaps(&self) -> impl Iterator<Item = &CoverageEntry> {
        self.entries
            .iter()
            .filter(|entry| matches!(entry.status, Status::Unclassified | Status::Uncovered))
    }
}

fn real(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

fn is_governed(info: &ModuleInfo, layers_declared: bool) -> bool {
    info.layer.is_some()
        || (info.feature.is_some() && !layers_declared)
        || info.wiring
        || matches!(info.kind, ModuleKind::Shared | ModuleKind::CompositionRoot)
}

fn classify(
    source: &SourceFile,
    parsed: &ParsedSource,
    files: &FileIndex,
    model: &ArchitectureModel<'_>,
) -> CoverageEntry {
    let info = model.info(&source.module);
    // In a workspace the architecture owning the module decides whether it declares layers.
    let layers_declared = !model.part_for(&source.module).config.layers.is_empty();
    let status = if info
        .as_deref()
        .is_some_and(|info| is_governed(info, layers_declared))
    {
        Status::Governed
    } else if source.is_package && parsed.syntax_error.is_none() && is_facade(parsed.module()) {
        Status::Facade
    } else {
        Status::Unclassified
    };
    let reason = match status {
        Status::Governed => "Explicit architecture policy.",
        Status::Facade => {
            "Package facade containing imports, documentation and package metadata only."
        }
        _ => "No architecture layer or explicit shared, wiring or composition-root policy.",
    };
    CoverageEntry {
        path: source.path.clone(),
        module: Some(source.module.clone()),
        status,
        kind: info.as_ref().map(|info| info.kind),
        feature: info.as_ref().and_then(|info| info.feature.clone()),
        layer: info.as_ref().and_then(|info| info.layer.clone()),
        wiring: info.as_ref().is_some_and(|info| info.wiring),
        central: info.as_ref().is_some_and(|info| info.central),
        reason: Some(reason.to_owned()),
        policies: info.as_deref().map(policies).unwrap_or_default(),
        source_root: files.source_root_dir(&source.module).map(str::to_owned),
    }
}

/// The architecture settings behind a classification, as `_policies`.
fn policies(info: &ModuleInfo) -> Vec<String> {
    let mut found = Vec::new();
    if let Some(layer) = &info.layer {
        found.push(format!("architecture.layers.{layer}"));
        // A central module is bound by an entry of its layer's `modules`.
        if info.central {
            found.push(format!("architecture.layers.{layer}.modules"));
        }
    }
    if info.feature.is_some() {
        found.push("architecture.features".to_owned());
    }
    if info.wiring {
        found.push("architecture.wiring".to_owned());
    }
    match info.kind {
        ModuleKind::Shared => found.push("architecture.shared".to_owned()),
        ModuleKind::CompositionRoot => found.push("architecture.composition_root".to_owned()),
        _ => {}
    }
    found
}

/// The dotted module of a file below an import root, if every part is an identifier.
fn module_name(path: &Path, root: &Path) -> Option<String> {
    let relative = path.strip_prefix(root).ok()?;
    let mut parts: Vec<String> = relative
        .components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect();
    if let Some(last) = parts.last_mut() {
        // `Path.with_suffix("")` drops only the final suffix.
        if let Some(dot) = last.rfind('.').filter(|dot| *dot > 0) {
            last.truncate(dot);
        }
    }
    if parts.last().is_some_and(|last| last == "__init__") {
        parts.pop();
    }
    (!parts.is_empty() && parts.iter().all(|part| is_identifier(part))).then(|| parts.join("."))
}

/// A package `__init__` holding only imports, documentation and package metadata.
fn is_facade(module: &ModModule) -> bool {
    let calls = version_calls(module);
    module
        .body
        .iter()
        .all(|statement| facade_statement(statement, &calls))
}

/// The spellings that call `importlib.metadata.version`.
fn version_calls(module: &ModModule) -> HashSet<String> {
    let mut calls = HashSet::new();
    for statement in walk_statements(module) {
        match statement {
            Stmt::ImportFrom(import) => {
                let source = import.module.as_ref().map(|name| name.id.as_str());
                for alias in &import.names {
                    let bound = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    match (source, alias.name.as_str()) {
                        (Some("importlib.metadata"), "version") => {
                            calls.insert(bound.to_owned());
                        }
                        (Some("importlib"), "metadata") => {
                            calls.insert(format!("{bound}.version"));
                        }
                        _ => {}
                    }
                }
            }
            Stmt::Import(import) => {
                for alias in &import.names {
                    if alias.name.as_str() == "importlib.metadata" {
                        let bound = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                        calls.insert(format!("{bound}.version"));
                    }
                }
            }
            _ => {}
        }
    }
    calls
}

fn facade_statement(statement: &Stmt, calls: &HashSet<String>) -> bool {
    match statement {
        Stmt::Import(_) | Stmt::ImportFrom(_) | Stmt::Pass(_) => true,
        Stmt::Expr(expression) => expression.value.is_string_literal_expr(),
        Stmt::Assign(StmtAssign { targets, value, .. }) => {
            metadata_assignment(targets.iter(), Some(value), calls)
        }
        Stmt::AnnAssign(StmtAnnAssign { target, value, .. }) => {
            metadata_assignment(std::iter::once(&**target), value.as_deref(), calls)
        }
        Stmt::If(branch) => {
            is_type_checking(&branch.test)
                && branch
                    .body
                    .iter()
                    .all(|child| facade_statement(child, calls))
                && branch.elif_else_clauses.iter().all(|clause| {
                    clause.test.as_ref().is_none_or(is_type_checking)
                        && clause
                            .body
                            .iter()
                            .all(|child| facade_statement(child, calls))
                })
        }
        Stmt::Try(attempt) if !attempt.is_star => {
            let handlers_safe = attempt.handlers.iter().all(|handler| {
                let ExceptHandler::ExceptHandler(handler) = handler;
                handler
                    .type_
                    .as_deref()
                    .is_none_or(|kind| matches!(kind, Expr::Name(_) | Expr::Attribute(_)))
            });
            let handler_bodies = attempt.handlers.iter().flat_map(|handler| {
                let ExceptHandler::ExceptHandler(handler) = handler;
                handler.body.iter()
            });
            handlers_safe
                && attempt
                    .body
                    .iter()
                    .chain(&attempt.orelse)
                    .chain(&attempt.finalbody)
                    .chain(handler_bodies)
                    .all(|child| facade_statement(child, calls))
        }
        _ => false,
    }
}

/// `ast.unparse(test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")`.
fn is_type_checking(test: &Expr) -> bool {
    dotted(test).is_some_and(|name| name == "TYPE_CHECKING" || name == "typing.TYPE_CHECKING")
}

/// The spelling of a name or attribute chain.
fn dotted(expression: &Expr) -> Option<String> {
    match expression {
        Expr::Name(name) => Some(name.id.to_string()),
        Expr::Attribute(attribute) => Some(format!(
            "{}.{}",
            dotted(&attribute.value)?,
            attribute.attr.id
        )),
        _ => None,
    }
}

/// `__all__` and `__version__` bound to a literal or to `version("<distribution>")`.
fn metadata_assignment<'a>(
    targets: impl Iterator<Item = &'a Expr>,
    value: Option<&Expr>,
    calls: &HashSet<String>,
) -> bool {
    let mut names = Vec::new();
    for target in targets {
        let Expr::Name(name) = target else {
            return false;
        };
        if !matches!(name.id.as_str(), "__all__" | "__version__") {
            return false;
        }
        names.push(name.id.as_str());
    }
    let Some(value) = value else {
        return false;
    };
    if literal(value).is_some() {
        return true;
    }
    let Expr::Call(call) = value else {
        return false;
    };
    names == ["__version__"]
        && dotted(&call.func).is_some_and(|name| calls.contains(&name))
        && call.arguments.args.len() == 1
        && call.arguments.args[0].is_string_literal_expr()
        && call.arguments.keywords.is_empty()
}

/// Whether `ast.literal_eval` accepts `expression`, and if so whether its value
/// is hashable; an unhashable set element or dict key raises `TypeError`.
fn literal(expression: &Expr) -> Option<bool> {
    match expression {
        Expr::StringLiteral(_)
        | Expr::BytesLiteral(_)
        | Expr::NumberLiteral(_)
        | Expr::BooleanLiteral(_)
        | Expr::NoneLiteral(_)
        | Expr::EllipsisLiteral(_) => Some(true),
        Expr::Tuple(tuple) => tuple
            .elts
            .iter()
            .map(literal)
            .collect::<Option<Vec<bool>>>()
            .map(|items| items.into_iter().all(|hashable| hashable)),
        Expr::List(list) => list
            .elts
            .iter()
            .try_for_each(|item| literal(item).map(drop))
            .map(|()| false),
        Expr::Set(set) => set
            .elts
            .iter()
            .all(|item| literal(item) == Some(true))
            .then_some(false),
        Expr::Dict(dict) => dict
            .items
            .iter()
            .all(|item| {
                item.key
                    .as_ref()
                    .is_some_and(|key| literal(key) == Some(true))
                    && literal(&item.value).is_some()
            })
            .then_some(false),
        Expr::Call(call) => (matches!(&*call.func, Expr::Name(name) if name.id.as_str() == "set")
            && call.arguments.args.is_empty()
            && call.arguments.keywords.is_empty())
        .then_some(false),
        Expr::UnaryOp(unary) => (matches!(unary.op, UnaryOp::UAdd | UnaryOp::USub)
            && number(&unary.operand).is_some())
        .then_some(true),
        Expr::BinOp(binary) => {
            let left = match &*binary.left {
                Expr::UnaryOp(unary) if matches!(unary.op, UnaryOp::UAdd | UnaryOp::USub) => {
                    number(&unary.operand)
                }
                other => number(other),
            };
            (matches!(binary.op, Operator::Add | Operator::Sub)
                && left.is_some_and(|complex| !complex)
                && number(&binary.right) == Some(true))
            .then_some(true)
        }
        _ => None,
    }
}

/// An `int`, `float` or `complex` constant, and whether it is complex.
fn number(expression: &Expr) -> Option<bool> {
    match expression {
        Expr::NumberLiteral(literal) => Some(matches!(literal.value, Number::Complex { .. })),
        _ => None,
    }
}
