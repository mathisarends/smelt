//! MOD001: `.value` read directly on a known `StrEnum` member.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::bindings::{BindingCounts, Coverage};
use crate::checker::analysis::namespace::{
    Namespace, resolve_dotted, resolve_export, resolve_relative,
};
use crate::checker::analysis::unparse::unparse;
use crate::checker::analysis::walk::{TreeNode, nodes_below, tree_nodes};
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::{Alias, Expr, ExprAttribute, ExprContext, ModModule, Stmt, StmtClassDef};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{HashMap, HashSet};

const RESERVED: [&str; 6] = [
    "value",
    "_ignore_",
    "__new__",
    "__init__",
    "__getattribute__",
    "__getattr__",
];

/// A class-body assignment that can define a member, in source order.
struct Assignment<'a> {
    targets: Vec<&'a str>,
    kind: AssignmentKind<'a>,
}

enum AssignmentKind<'a> {
    Text,
    Alias(&'a str),
    Call(Vec<&'a str>),
}

/// A uniquely bound top-level class with one dotted base and a plain body.
struct Candidate<'a> {
    name: &'a str,
    base: Vec<&'a str>,
    assignments: Vec<Assignment<'a>>,
}

struct Access<'a> {
    receiver: Vec<&'a str>,
    member: &'a str,
    node: &'a ExprAttribute,
}

/// The enum inputs of one module.
struct ModuleEnums<'a> {
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    candidates: Vec<Candidate<'a>>,
    accesses: Vec<Access<'a>>,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let mut sources: Vec<&(&SourceFile, &ParsedSource)> = context.sources.iter().collect();
    sources.sort_by(|left, right| left.0.path.cmp(&right.0.path));
    let scanned: Vec<(ModuleEnums<'_>, Namespace)> = sources
        .par_iter()
        .map(|(source, parsed)| module_enums(source, parsed))
        .collect();
    let mut modules = Vec::with_capacity(scanned.len());
    let mut namespaces: HashMap<&str, Namespace> = HashMap::with_capacity(scanned.len());
    for (facts, namespace) in scanned {
        namespaces.insert(facts.source.module.as_str(), namespace);
        modules.push(facts);
    }
    for package in &context.files.packages {
        namespaces
            .entry(package.as_str())
            .or_insert_with(empty_namespace);
    }
    namespaces.entry("enum").or_insert_with(|| Namespace {
        imported: HashMap::new(),
        defined: HashSet::from(["StrEnum".to_owned(), "auto".to_owned()]),
    });
    let shadowed = context.files.sources.contains_key("enum");
    let enums = Enums { namespaces };
    let mut members: HashMap<String, HashSet<&str>> = HashMap::new();
    for facts in &modules {
        let module = facts.source.module.as_str();
        for candidate in &facts.candidates {
            let defined = if shadowed {
                HashSet::new()
            } else {
                enums.members(candidate, module)
            };
            members.insert(format!("{module}.{}", candidate.name), defined);
        }
    }
    let mut found = Vec::new();
    for facts in &modules {
        let module = facts.source.module.as_str();
        for access in &facts.accesses {
            let Some(resolved) = enums.resolve(&access.receiver, module) else {
                continue;
            };
            if members
                .get(&resolved)
                .is_some_and(|defined| defined.contains(access.member))
            {
                found.push(finding(facts, access, &resolved));
            }
        }
    }
    found
}

fn finding(facts: &ModuleEnums<'_>, access: &Access<'_>, enum_name: &str) -> StructureFinding {
    let text = &facts.parsed.text;
    let (line, column) = text.position(usize::from(access.node.start()));
    let (end_line, end_column) = text.position(usize::from(access.node.end()));
    let replacement = unparse(&text.text, &access.node.value);
    StructureFinding {
        message: format!("consider {replacement} directly; StrEnum members are already strings"),
        path: facts.source.path.clone(),
        position: Some((line, column + 1)),
        end_line: Some(end_line),
        end_column: Some(end_column + 1),
        source_module: Some(facts.source.module.clone()),
        expected: json!({
            "enum": enum_name,
            "member": access.member,
            "replacement": replacement,
            "automatic_rewrite": false,
        }),
        hint: format!(
            "Use {replacement} if the consumer accepts string subclasses; retain an explicit \
             conversion when it requires an exact str."
        ),
        ..StructureFinding::default()
    }
}

fn empty_namespace() -> Namespace {
    Namespace {
        imported: HashMap::new(),
        defined: HashSet::new(),
    }
}

struct Enums<'a> {
    namespaces: HashMap<&'a str, Namespace>,
}

impl Enums<'_> {
    fn resolve(&self, chain: &[&str], module: &str) -> Option<String> {
        let (owner, name) = self.target(chain, module)?;
        Some(format!("{owner}.{}", name?))
    }

    fn target(&self, chain: &[&str], module: &str) -> Option<(String, Option<String>)> {
        let (first, attributes) = chain.split_first()?;
        let qualified = self
            .namespaces
            .get(module)?
            .imported
            .get(*first)
            .cloned()
            .unwrap_or_else(|| format!("{module}.{first}"));
        let mut resolved = resolve_dotted(&qualified, &self.namespaces);
        for attribute in attributes {
            // Attributes of an already resolved class or member are unknown, rather
            // than another spelling of that class.
            let (current, None) = resolved? else {
                return None;
            };
            resolved = resolve_export(&current, attribute, &self.namespaces);
        }
        resolved
    }

    fn members<'c>(&self, candidate: &Candidate<'c>, module: &str) -> HashSet<&'c str> {
        let mut members = HashSet::new();
        if self.resolve(&candidate.base, module).as_deref() != Some("enum.StrEnum") {
            return members;
        }
        for assignment in &candidate.assignments {
            let defines = match &assignment.kind {
                AssignmentKind::Text => true,
                AssignmentKind::Alias(name) => members.contains(name),
                AssignmentKind::Call(callee) => {
                    self.resolve(callee, module).as_deref() == Some("enum.auto")
                }
            };
            if defines {
                members.extend(assignment.targets.iter().copied());
            }
        }
        members
    }
}

fn module_enums<'a>(
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
) -> (ModuleEnums<'a>, Namespace) {
    let tree = parsed.module();
    let bound = BindingCounts::new(tree, Coverage::Full);
    let namespace = module_namespace(source, tree, &bound);
    let candidates = if bound.get("*") > 0 {
        Vec::new()
    } else {
        candidates(tree, &bound)
    };
    let facts = ModuleEnums {
        source,
        parsed,
        candidates,
        accesses: accesses(tree),
    };
    (facts, namespace)
}

/// A binding shadowed even in another scope is unknown. This intentionally trades
/// some recognition for avoiding guesses about Python name resolution.
fn module_namespace(source: &SourceFile, tree: &ModModule, bound: &BindingCounts<'_>) -> Namespace {
    let mut imported = HashMap::new();
    if bound.get("*") == 0 {
        for statement in &tree.body {
            match statement {
                Stmt::Import(node) => {
                    for alias in &node.names {
                        let name = imported_name(alias);
                        if bound.get(name) == 1 {
                            let target = if alias.asname.is_some() {
                                alias.name.as_str()
                            } else {
                                name
                            };
                            imported.insert(name.to_owned(), target.to_owned());
                        }
                    }
                }
                Stmt::ImportFrom(node) => {
                    let base = resolve_relative(
                        &source.module,
                        source.is_package,
                        node.level,
                        node.module.as_ref().map(|name| name.as_str()),
                    );
                    for alias in &node.names {
                        let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                        if bound.get(name) == 1 && name != "*" {
                            imported.insert(name.to_owned(), format!("{base}.{}", alias.name));
                        }
                    }
                }
                _ => {}
            }
        }
    }
    // Block fallback resolution to a submodule for uncertain or rebound names.
    let defined = bound
        .names()
        .filter(|name| !imported.contains_key(*name))
        .map(str::to_owned)
        .collect();
    Namespace { imported, defined }
}

fn imported_name(alias: &Alias) -> &str {
    match &alias.asname {
        Some(name) => name.as_str(),
        None => alias.name.split('.').next().unwrap_or_default(),
    }
}

fn accesses(tree: &ModModule) -> Vec<Access<'_>> {
    let mut found = Vec::new();
    for node in tree_nodes(tree) {
        let TreeNode::Expression(Expr::Attribute(outer)) = node else {
            continue;
        };
        if outer.ctx != ExprContext::Load || outer.attr.as_str() != "value" {
            continue;
        }
        let Expr::Attribute(inner) = outer.value.as_ref() else {
            continue;
        };
        if let Some(receiver) = chain(&inner.value) {
            found.push(Access {
                receiver,
                member: inner.attr.as_str(),
                node: outer,
            });
        }
    }
    found
}

fn candidates<'a>(tree: &'a ModModule, bound: &BindingCounts<'a>) -> Vec<Candidate<'a>> {
    let mut found = Vec::new();
    for statement in &tree.body {
        let Stmt::ClassDef(class) = statement else {
            continue;
        };
        let [base] = class.bases() else {
            continue;
        };
        if bound.get(class.name.as_str()) != 1
            || !class.keywords().is_empty()
            || !class.decorator_list.is_empty()
            || reserves_names(class)
        {
            continue;
        }
        if let Some(base) = chain(base) {
            found.push(Candidate {
                name: class.name.as_str(),
                base,
                assignments: assignments(class),
            });
        }
    }
    found
}

fn is_reserved(name: &str) -> bool {
    RESERVED.contains(&name)
}

fn reserves_names(class: &StmtClassDef) -> bool {
    class.body.iter().any(|child| match child {
        Stmt::Assign(_) | Stmt::AnnAssign(_) | Stmt::AugAssign(_) => {
            nodes_below(TreeNode::Statement(child))
                .into_iter()
                .any(|node| match node {
                    TreeNode::Expression(Expr::Name(name)) => {
                        name.ctx != ExprContext::Load && is_reserved(name.id.as_str())
                    }
                    TreeNode::Parameter(parameter) => is_reserved(parameter.name.as_str()),
                    _ => false,
                })
        }
        Stmt::FunctionDef(node) => is_reserved(node.name.as_str()),
        Stmt::ClassDef(node) => is_reserved(node.name.as_str()),
        Stmt::Import(node) => node
            .names
            .iter()
            .any(|alias| is_reserved(imported_name(alias))),
        Stmt::ImportFrom(node) => node
            .names
            .iter()
            .any(|alias| is_reserved(imported_name(alias))),
        Stmt::Global(node) => node.names.iter().any(|name| is_reserved(name.as_str())),
        Stmt::Nonlocal(node) => node.names.iter().any(|name| is_reserved(name.as_str())),
        _ => false,
    })
}

fn assignments(class: &StmtClassDef) -> Vec<Assignment<'_>> {
    let mut found = Vec::new();
    for child in &class.body {
        let (targets, value): (&[Expr], Option<&Expr>) = match child {
            Stmt::Assign(node) => (&node.targets, Some(&node.value)),
            Stmt::AnnAssign(node) => (
                std::slice::from_ref(node.target.as_ref()),
                node.value.as_deref(),
            ),
            _ => continue,
        };
        let names: Vec<&str> = targets
            .iter()
            .filter_map(Expr::as_name_expr)
            .map(|target| target.id.as_str())
            .filter(|name| !name.starts_with('_'))
            .collect();
        let kind = match value {
            Some(Expr::StringLiteral(_)) => AssignmentKind::Text,
            Some(Expr::Name(name)) => AssignmentKind::Alias(name.id.as_str()),
            Some(Expr::Call(call))
                if call.arguments.args.is_empty() && call.arguments.keywords.is_empty() =>
            {
                let Some(callee) = chain(&call.func) else {
                    continue;
                };
                AssignmentKind::Call(callee)
            }
            _ => continue,
        };
        found.push(Assignment {
            targets: names,
            kind,
        });
    }
    found
}

/// A dotted name like `enum.StrEnum`; other expressions never resolve.
fn chain(node: &Expr) -> Option<Vec<&str>> {
    match node {
        Expr::Name(item) => Some(vec![item.id.as_str()]),
        Expr::Attribute(item) => {
            let mut base = chain(&item.value)?;
            base.push(item.attr.as_str());
            Some(base)
        }
        _ => None,
    }
}
