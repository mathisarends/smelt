//! One literal lazy-export table and its verified importlib forwarding handler.
use super::dump::dump_stmt;
use crate::checker::files::is_identifier;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    CmpOp, Expr, ExprContext, ExprDict, InterpolatedStringElement, InterpolatedStringElements,
    ModModule, Stmt, StmtFunctionDef,
};
use ruff_python_parser::parse_module;
use std::collections::{HashMap, HashSet};

const MIN_HANDLER: usize = 4;
const CACHED_HANDLER: usize = 5;
const DISTINCT_LOCALS: usize = 5;
const RESERVED_BUILTINS: [&str; 3] = ["getattr", "globals", "AttributeError"];
const STABLE_BUILTINS: [&str; 7] = [
    "getattr",
    "globals",
    "AttributeError",
    "sorted",
    "list",
    "tuple",
    "set",
];
const DYNAMIC_CALLS: [&str; 5] = ["exec", "eval", "globals", "locals", "vars"];
const TABLE_CALLS: [&str; 3] = ["sorted", "list", "tuple"];

/// `None` means unsupported or dynamic; a stub or a table alone proves nothing.
pub fn static_lazy_exports(source: &str, tree: &ModModule) -> Option<HashMap<String, String>> {
    let mut handlers = tree.body.iter().filter_map(|statement| match statement {
        Stmt::FunctionDef(node) if !node.is_async && node.name.as_str() == "__getattr__" => {
            Some((statement, node))
        }
        _ => None,
    });
    let (handler_statement, handler) = handlers.next()?;
    if handlers.next().is_some() {
        return None;
    }
    let imports = importlib_bindings(tree);
    for statement in &tree.body {
        let (targets, value): (&[Expr], &Expr) = match statement {
            Stmt::Assign(node) => (&node.targets, &node.value),
            Stmt::AnnAssign(node) => match &node.value {
                Some(value) => (std::slice::from_ref(node.target.as_ref()), value),
                None => continue,
            },
            _ => continue,
        };
        let Expr::Dict(dict) = value else {
            continue;
        };
        let [Expr::Name(table)] = targets else {
            continue;
        };
        let table = table.id.as_str();
        let Some(entries) = entries(dict) else {
            continue;
        };
        if !forwards(source, handler, table, &imports) {
            continue;
        }
        if !stable(source, tree, handler_statement, table, statement)
            || shadows_exports(tree, &entries)
        {
            continue;
        }
        let Some(public) = public_names(tree, table, &entries) else {
            continue;
        };
        return Some(
            entries
                .into_iter()
                .filter(|(name, _)| public.contains(name) && !name.starts_with('_'))
                .collect(),
        );
    }
    None
}

fn string_value(expression: &Expr) -> Option<&str> {
    match expression {
        Expr::StringLiteral(node) => Some(node.value.to_str()),
        _ => None,
    }
}

fn entries(node: &ExprDict) -> Option<HashMap<String, String>> {
    let mut result = HashMap::with_capacity(node.items.len());
    for item in &node.items {
        let key = string_value(item.key.as_ref()?)?;
        if result.contains_key(key) || !is_identifier(key) {
            return None;
        }
        let elements: &[Expr] = match &item.value {
            Expr::Tuple(value) => &value.elts,
            Expr::List(value) => &value.elts,
            _ => return None,
        };
        let [module, name] = elements else {
            return None;
        };
        let (module, name) = (string_value(module)?, string_value(name)?);
        if module.is_empty() || !module.split('.').all(is_identifier) || !is_identifier(name) {
            return None;
        }
        result.insert(key.to_owned(), format!("{module}.{name}"));
    }
    Some(result)
}

fn importlib_bindings(tree: &ModModule) -> HashSet<String> {
    let mut names = HashSet::new();
    for statement in &tree.body {
        match statement {
            Stmt::ImportFrom(node)
                if node.level == 0
                    && node.module.as_ref().is_some_and(|name| name == "importlib") =>
            {
                for alias in &node.names {
                    if alias.name.as_str() == "import_module" {
                        let local = alias.asname.as_ref().unwrap_or(&alias.name);
                        names.insert(local.to_string());
                    }
                }
            }
            Stmt::Import(node) => {
                for alias in &node.names {
                    if alias.name.as_str() == "importlib" {
                        let local = alias.asname.as_ref().unwrap_or(&alias.name);
                        names.insert(format!("{local}.import_module"));
                    }
                }
            }
            _ => {}
        }
    }
    names
}

fn root_of(name: &str) -> &str {
    name.split('.').next().unwrap_or(name)
}

fn dumps(source: &str, body: &[Stmt]) -> Vec<String> {
    body.iter()
        .map(|statement| dump_stmt(source, statement))
        .collect()
}

fn forwards(
    source: &str,
    handler: &StmtFunctionDef,
    table: &str,
    imports: &HashSet<String>,
) -> bool {
    let parameters = &handler.parameters;
    let mut positional = parameters.posonlyargs.iter().chain(&parameters.args);
    let (Some(only), None) = (positional.next(), positional.next()) else {
        return false;
    };
    let plain_annotation = |annotation: Option<&Expr>| {
        annotation.is_none_or(|node| {
            matches!(
                node,
                Expr::Name(_)
                    | Expr::StringLiteral(_)
                    | Expr::BytesLiteral(_)
                    | Expr::NumberLiteral(_)
                    | Expr::BooleanLiteral(_)
                    | Expr::NoneLiteral(_)
                    | Expr::EllipsisLiteral(_)
            )
        })
    };
    if !handler.decorator_list.is_empty()
        || parameters.vararg.is_some()
        || parameters.kwarg.is_some()
        || !parameters.kwonlyargs.is_empty()
        || only.default.is_some()
        || handler.type_params.is_some()
        || !plain_annotation(only.parameter.annotation.as_deref())
        || !plain_annotation(handler.returns.as_deref())
    {
        return false;
    }
    let body = &handler.body;
    if !(MIN_HANDLER..=CACHED_HANDLER).contains(&body.len()) {
        return false;
    }
    let argument = only.parameter.name.as_str();
    if !guards(&body[0], argument, table) {
        return false;
    }
    let Some((module, attribute, value)) = forwarded_variables(&body[1], &body[2]) else {
        return false;
    };
    let local: HashSet<&str> = [argument, module, attribute, value].into_iter().collect();
    let mut distinct = local.clone();
    distinct.insert(table);
    if distinct.len() != DISTINCT_LOCALS
        || local.iter().any(|name| {
            RESERVED_BUILTINS.contains(name) || imports.iter().any(|item| root_of(item) == *name)
        })
    {
        return false;
    }
    let cache = if body.len() == CACHED_HANDLER {
        format!("globals()[{argument}] = {value}\n")
    } else {
        String::new()
    };
    let actual = dumps(source, &body[1..]);
    imports.iter().any(|importer| {
        let text = format!(
            "{module}, {attribute} = {table}[{argument}]\n{value} = getattr({importer}({module}), {attribute})\n{cache}return {value}"
        );
        parse_module(&text).is_ok_and(|parsed| actual == dumps(&text, &parsed.syntax().body))
    })
}

fn guards(guard: &Stmt, argument: &str, table: &str) -> bool {
    let Stmt::If(node) = guard else {
        return false;
    };
    if !node.elif_else_clauses.is_empty() {
        return false;
    }
    let [Stmt::Raise(raised)] = &*node.body else {
        return false;
    };
    if raised.cause.is_some() {
        return false;
    }
    let Some(Expr::Call(exception)) = raised.exc.as_deref() else {
        return false;
    };
    if !matches!(exception.func.as_ref(), Expr::Name(name) if name.id.as_str() == "AttributeError")
        || !exception.arguments.keywords.is_empty()
        || !exception
            .arguments
            .args
            .iter()
            .all(|value| plain_message(value, argument))
    {
        return false;
    }
    let Expr::Compare(test) = node.test.as_ref() else {
        return false;
    };
    matches!(
        (&*test.ops, &*test.operands),
        ([CmpOp::NotIn], [Expr::Name(left), Expr::Name(right)])
            if left.id.as_str() == argument && right.id.as_str() == table
    )
}

/// Only constants, f-strings and the requested name may build the error message.
fn plain_message(expression: &Expr, argument: &str) -> bool {
    match expression {
        Expr::StringLiteral(_)
        | Expr::BytesLiteral(_)
        | Expr::NumberLiteral(_)
        | Expr::BooleanLiteral(_)
        | Expr::NoneLiteral(_)
        | Expr::EllipsisLiteral(_) => true,
        Expr::Name(name) => {
            matches!(name.id.as_str(), value if value == argument || value == "__name__")
        }
        Expr::FString(item) => item.value.iter().all(|part| match part {
            ruff_python_ast::FStringPartRef::Literal(_) => true,
            ruff_python_ast::FStringPartRef::FString(fstring) => {
                plain_elements(&fstring.elements, argument)
            }
        }),
        _ => false,
    }
}

fn plain_elements(elements: &InterpolatedStringElements, argument: &str) -> bool {
    elements.iter().all(|element| match element {
        InterpolatedStringElement::Literal(_) => true,
        InterpolatedStringElement::Interpolation(value) => {
            plain_message(&value.expression, argument)
                && value
                    .format_spec
                    .as_ref()
                    .is_none_or(|spec| plain_elements(&spec.elements, argument))
        }
    })
}

fn forwarded_variables<'a>(
    lookup: &'a Stmt,
    load: &'a Stmt,
) -> Option<(&'a str, &'a str, &'a str)> {
    let Stmt::Assign(lookup) = lookup else {
        return None;
    };
    let [Expr::Tuple(target)] = &*lookup.targets else {
        return None;
    };
    let [Expr::Name(module), Expr::Name(attribute)] = &*target.elts else {
        return None;
    };
    let Stmt::Assign(load) = load else {
        return None;
    };
    let [Expr::Name(value)] = &*load.targets else {
        return None;
    };
    Some((module.id.as_str(), attribute.id.as_str(), value.id.as_str()))
}

#[derive(Default)]
struct WriteFacts<'a> {
    writes: HashMap<&'a str, usize>,
    imports: HashMap<&'a str, usize>,
    scoped: bool,
}

impl<'a> Visitor<'a> for WriteFacts<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            Stmt::FunctionDef(node) => *self.writes.entry(node.name.as_str()).or_insert(0) += 1,
            Stmt::ClassDef(node) => *self.writes.entry(node.name.as_str()).or_insert(0) += 1,
            Stmt::Import(node) => {
                for alias in &node.names {
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    *self.imports.entry(root_of(name)).or_insert(0) += 1;
                }
            }
            Stmt::ImportFrom(node) => {
                for alias in &node.names {
                    let name = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
                    *self.imports.entry(root_of(name)).or_insert(0) += 1;
                }
            }
            Stmt::Global(_) | Stmt::Nonlocal(_) => self.scoped = true,
            _ => {}
        }
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(node) = expression
            && node.ctx != ExprContext::Load
        {
            *self.writes.entry(node.id.as_str()).or_insert(0) += 1;
        }
        visitor::walk_expr(self, expression);
    }
}

fn stable(source: &str, tree: &ModModule, handler: &Stmt, table: &str, declaration: &Stmt) -> bool {
    let mut facts = WriteFacts::default();
    facts.visit_body(&tree.body);
    if facts.scoped {
        return false;
    }
    let writes = |name: &str| facts.writes.get(name).copied().unwrap_or(0);
    let imports = |name: &str| facts.imports.get(name).copied().unwrap_or(0);
    if writes(table) != 1
        || writes("__getattr__") != 1
        || imports(table) != 0
        || STABLE_BUILTINS
            .iter()
            .any(|name| writes(name) != 0 || imports(name) != 0)
    {
        return false;
    }
    let bindings = importlib_bindings(tree);
    let roots: HashSet<&str> = bindings.iter().map(|name| root_of(name)).collect();
    if roots
        .iter()
        .any(|root| writes(root) != 0 || imports(root) != 1)
        || writes("__all__") > 1
    {
        return false;
    }
    let allowed = allowed_statements(source, tree, handler, table, declaration);
    let mut references = References {
        allowed: &allowed,
        table,
        found: false,
    };
    references.visit_body(&tree.body);
    if references.found {
        return false;
    }
    let mut attributes = ModuleAttributeWrites {
        modules: &roots,
        found: false,
    };
    attributes.visit_body(&tree.body);
    !attributes.found
}

fn allowed_statements(
    source: &str,
    tree: &ModModule,
    handler: &Stmt,
    table: &str,
    declaration: &Stmt,
) -> HashSet<*const Stmt> {
    let mut allowed: HashSet<*const Stmt> = HashSet::new();
    allowed.insert(handler);
    allowed.insert(declaration);
    let expected = format!("return sorted(set(globals()) | set({table}))");
    let expected_dump = parse_module(&expected)
        .ok()
        .map(|parsed| dumps(&expected, &parsed.syntax().body));
    for statement in &tree.body {
        match statement {
            Stmt::FunctionDef(node) if !node.is_async && node.name.as_str() == "__dir__" => {
                let parameters = &node.parameters;
                if node.decorator_list.is_empty()
                    && parameters.posonlyargs.is_empty()
                    && parameters.args.is_empty()
                    && parameters.kwonlyargs.is_empty()
                    && parameters.vararg.is_none()
                    && parameters.kwarg.is_none()
                    && expected_dump.as_ref() == Some(&dumps(source, &node.body))
                {
                    allowed.extend(node.body.iter().map(std::ptr::from_ref));
                }
            }
            Stmt::Assign(node) if node.targets.iter().any(is_all) => {
                allowed.insert(statement);
            }
            Stmt::AnnAssign(node) if is_all(&node.target) => {
                allowed.insert(statement);
            }
            _ => {}
        }
    }
    allowed
}

fn is_all(target: &Expr) -> bool {
    matches!(target, Expr::Name(name) if name.id.as_str() == "__all__")
}

struct References<'r> {
    allowed: &'r HashSet<*const Stmt>,
    table: &'r str,
    found: bool,
}

impl<'a> Visitor<'a> for References<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if !self.allowed.contains(&std::ptr::from_ref(statement)) {
            visitor::walk_stmt(self, statement);
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Name(node) if matches!(node.id.as_str(), name if name == self.table || name == "__all__") =>
            {
                self.found = true;
            }
            Expr::Call(node)
                if matches!(
                    node.func.as_ref(),
                    Expr::Name(name) if DYNAMIC_CALLS.contains(&name.id.as_str())
                ) =>
            {
                self.found = true;
            }
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
}

struct ModuleAttributeWrites<'r> {
    modules: &'r HashSet<&'r str>,
    found: bool,
}

impl<'a> Visitor<'a> for ModuleAttributeWrites<'_> {
    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Attribute(node) = expression
            && node.ctx != ExprContext::Load
            && matches!(node.value.as_ref(), Expr::Name(name) if self.modules.contains(name.id.as_str()))
        {
            self.found = true;
        }
        visitor::walk_expr(self, expression);
    }
}

fn shadows_exports(tree: &ModModule, names: &HashMap<String, String>) -> bool {
    let mut finder = ExportShadow {
        names,
        found: false,
    };
    finder.visit_body(&tree.body);
    finder.found
}

struct ExportShadow<'r> {
    names: &'r HashMap<String, String>,
    found: bool,
}

impl ExportShadow<'_> {
    fn alias_shadows(&self, alias: &ruff_python_ast::Alias) -> bool {
        let local = alias.asname.as_ref().unwrap_or(&alias.name).as_str();
        alias.name.as_str() == "*" || self.names.contains_key(root_of(local))
    }
}

impl<'a> Visitor<'a> for ExportShadow<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let shadowed = match statement {
            Stmt::FunctionDef(node) => self.names.contains_key(node.name.as_str()),
            Stmt::ClassDef(node) => self.names.contains_key(node.name.as_str()),
            Stmt::Import(node) => node.names.iter().any(|alias| self.alias_shadows(alias)),
            Stmt::ImportFrom(node) => node.names.iter().any(|alias| self.alias_shadows(alias)),
            _ => false,
        };
        self.found |= shadowed;
        visitor::walk_stmt(self, statement);
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if let Expr::Name(node) = expression
            && node.ctx != ExprContext::Load
            && self.names.contains_key(node.id.as_str())
        {
            self.found = true;
        }
        visitor::walk_expr(self, expression);
    }
}

fn public_names(
    tree: &ModModule,
    table: &str,
    names: &HashMap<String, String>,
) -> Option<HashSet<String>> {
    let mut declarations: Vec<&Expr> = Vec::new();
    for statement in &tree.body {
        let (targets, value): (&[Expr], Option<&Expr>) = match statement {
            Stmt::Assign(node) => (&node.targets, Some(&node.value)),
            Stmt::AnnAssign(node) => (
                std::slice::from_ref(node.target.as_ref()),
                node.value.as_deref(),
            ),
            _ => continue,
        };
        if let Some(value) = value
            && targets.iter().any(is_all)
        {
            if targets.len() != 1 {
                return None;
            }
            declarations.push(value);
        }
    }
    let all_names = || names.keys().cloned().collect::<HashSet<_>>();
    let value = match declarations.as_slice() {
        [] => return Some(all_names()),
        [value] => *value,
        _ => return None,
    };
    if let Expr::Call(call) = value
        && matches!(call.func.as_ref(), Expr::Name(name) if TABLE_CALLS.contains(&name.id.as_str()))
        && call.arguments.keywords.is_empty()
        && matches!(&*call.arguments.args, [Expr::Name(name)] if name.id.as_str() == table)
    {
        return Some(all_names());
    }
    let elements: &[Expr] = match value {
        Expr::List(node) => &node.elts,
        Expr::Tuple(node) => &node.elts,
        Expr::Set(node) => &node.elts,
        _ => return None,
    };
    let mut public = HashSet::new();
    for element in elements {
        let text = string_value(element)?;
        if names.contains_key(text) {
            public.insert(text.to_owned());
        }
    }
    Some(public)
}
