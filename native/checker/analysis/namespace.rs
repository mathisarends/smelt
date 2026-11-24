//! The names a module binds at top level, like `smelt.analysis.exports.namespace_of`.
use super::lazy_exports::static_lazy_exports;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{ExceptHandler, Expr, ModModule, Stmt};
use std::collections::{HashMap, HashSet};

// Re-export chains are short; a longer one is more likely a cycle than a design.
const MAX_HOPS: usize = 8;

pub struct Namespace {
    /// Local name to the absolute name it was imported as.
    pub imported: HashMap<String, String>,
    pub defined: HashSet<String>,
}

pub fn namespace_of(source: &str, tree: &ModModule, module: &str, is_package: bool) -> Namespace {
    let mut namespace = Namespace {
        imported: HashMap::new(),
        defined: HashSet::new(),
    };
    namespace.top_level(&tree.body, module, is_package);
    if let Some(lazy) = static_lazy_exports(source, tree) {
        for (name, target) in lazy {
            if !namespace.defined.contains(&name) && !namespace.imported.contains_key(&name) {
                namespace.imported.insert(name, target);
            }
        }
    }
    namespace
}

/// Resolve `from <level dots><target> import ...` inside `module`.
pub fn resolve_relative(
    module: &str,
    is_package: bool,
    level: u32,
    target: Option<&str>,
) -> String {
    if level == 0 {
        return target.unwrap_or_default().to_owned();
    }
    let mut parts: Vec<&str> = module.split('.').collect();
    if !is_package {
        parts.pop();
    }
    if level > 1 {
        let removed = usize::try_from(level - 1).unwrap_or(usize::MAX);
        let length = parts.len();
        // Python slices `parts[:length - removed]`; a negative end counts from the back.
        let end = if removed <= length {
            length - removed
        } else {
            length.saturating_sub(removed - length)
        };
        parts.truncate(end);
    }
    let base = parts.join(".");
    match target {
        Some(target) if !target.is_empty() && !base.is_empty() => format!("{base}.{target}"),
        Some(target) if !target.is_empty() => target.to_owned(),
        _ => base,
    }
}

/// Where a dotted name such as `app.billing.Invoice` is defined.
///
/// The longest module prefix short of the last segment is imported as a module; the
/// remaining segments are attributes, so a facade binding wins over a submodule.
pub fn resolve_dotted(
    qualified: &str,
    namespaces: &HashMap<&str, Namespace>,
) -> Option<(String, Option<String>)> {
    let parts: Vec<&str> = qualified.split('.').collect();
    let Some(end) = (1..parts.len())
        .rev()
        .find(|end| namespaces.contains_key(parts[..*end].join(".").as_str()))
    else {
        return namespaces
            .contains_key(qualified)
            .then(|| (qualified.to_owned(), None));
    };
    let mut current = Some((parts[..end].join("."), None));
    for attribute in &parts[end..] {
        current = match current {
            Some((module, None)) => resolve_export(&module, attribute, namespaces),
            other => return other,
        };
    }
    current
}

/// The module defining `module.name` and its name there; `None` as the name when it
/// is a module itself.
pub fn resolve_export(
    module: &str,
    name: &str,
    namespaces: &HashMap<&str, Namespace>,
) -> Option<(String, Option<String>)> {
    let (mut module, mut name) = (module.to_owned(), name.to_owned());
    let mut seen = HashSet::new();
    while seen.len() < MAX_HOPS && seen.insert((module.clone(), name.clone())) {
        let namespace = namespaces.get(module.as_str())?;
        if namespace.defined.contains(&name) {
            return Some((module, Some(name)));
        }
        let own = format!("{module}.{name}");
        let target = namespace
            .imported
            .get(&name)
            .filter(|target| !target.is_empty())
            .cloned()
            .unwrap_or_else(|| own.clone());
        if namespaces.contains_key(target.as_str()) {
            return Some((target, None));
        }
        if target == own {
            return None;
        }
        let (parent, last) = target.rsplit_once('.')?;
        if parent.is_empty() {
            return None;
        }
        (module, name) = (parent.to_owned(), last.to_owned());
    }
    None
}

impl Namespace {
    fn top_level(&mut self, body: &[Stmt], module: &str, is_package: bool) {
        for statement in body {
            match statement {
                Stmt::If(node) => {
                    self.top_level(&node.body, module, is_package);
                    for clause in &node.elif_else_clauses {
                        self.top_level(&clause.body, module, is_package);
                    }
                }
                Stmt::Try(node) if !node.is_star => {
                    self.top_level(&node.body, module, is_package);
                    self.top_level(&node.orelse, module, is_package);
                    self.top_level(&node.finalbody, module, is_package);
                    for handler in &node.handlers {
                        let ExceptHandler::ExceptHandler(handler) = handler;
                        self.top_level(&handler.body, module, is_package);
                    }
                }
                Stmt::Import(node) => {
                    for alias in &node.names {
                        let full = alias.name.as_str();
                        let (name, target) = match &alias.asname {
                            Some(local) => (local.as_str(), full),
                            None => {
                                let top = full.split('.').next().unwrap_or(full);
                                (top, top)
                            }
                        };
                        self.imported.insert(name.to_owned(), target.to_owned());
                        self.defined.remove(name);
                    }
                }
                Stmt::ImportFrom(node) => {
                    let base = resolve_relative(
                        module,
                        is_package,
                        node.level,
                        node.module.as_ref().map(|name| name.as_str()),
                    );
                    for alias in &node.names {
                        if alias.name.as_str() == "*" {
                            continue;
                        }
                        let qualified = if base.is_empty() {
                            alias.name.to_string()
                        } else {
                            format!("{base}.{}", alias.name)
                        };
                        let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                        self.imported.insert(name.to_owned(), qualified);
                        self.defined.remove(name);
                    }
                }
                other => {
                    for name in defined_names(other) {
                        self.imported.remove(name);
                        self.defined.insert(name.to_owned());
                    }
                }
            }
        }
    }
}

fn defined_names(statement: &Stmt) -> Vec<&str> {
    let targets: &[Expr] = match statement {
        Stmt::FunctionDef(node) => return vec![node.name.as_str()],
        Stmt::ClassDef(node) => return vec![node.name.as_str()],
        Stmt::Assign(node) => &node.targets,
        Stmt::AnnAssign(node) => std::slice::from_ref(node.target.as_ref()),
        Stmt::AugAssign(node) => std::slice::from_ref(node.target.as_ref()),
        Stmt::TypeAlias(node) => std::slice::from_ref(node.name.as_ref()),
        _ => return Vec::new(),
    };
    let mut collector = NameCollector { found: Vec::new() };
    for target in targets {
        collector.visit_expr(target);
    }
    collector.found
}

struct NameCollector<'a> {
    found: Vec<&'a str>,
}

impl<'a> Visitor<'a> for NameCollector<'a> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(node) = expression {
            self.found.push(node.id.as_str());
        }
        visitor::walk_expr(self, expression);
    }
}
