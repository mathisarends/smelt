//! IDM009: `idioms/redundant-getattr`, a literal `getattr` or `hasattr` on an
//! attribute that every project class of the receiver's declared type declares.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::bindings::BindingCounts;
use crate::checker::analysis::class_attributes::{ClassAttributes, Lookup, Value};
use crate::checker::analysis::namespace::project_namespaces;
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, ExprCall, Stmt, StmtFunctionDef};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{HashMap, HashSet};

// Decorators that keep the first parameter an instance of the class.
const RECEIVER_DECORATORS: [&str; 5] = [
    "builtins.property",
    "functools.cached_property",
    "abc.abstractmethod",
    "typing.override",
    "typing_extensions.override",
];

// Methods that may run before the attributes `__init__` assigns exist.
const CONSTRUCTION: [&str; 5] = [
    "__init__",
    "__new__",
    "__post_init__",
    "__init_subclass__",
    "__setstate__",
];

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let namespaces = project_namespaces(context.sources, &context.files.packages);
    let index = ClassAttributes::new(&namespaces, context.sources);
    let mut sources: Vec<&(&SourceFile, &ParsedSource)> = context.sources.iter().collect();
    sources.sort_by(|left, right| left.0.module.cmp(&right.0.module));
    sources
        .par_iter()
        .flat_map_iter(|&&(source, parsed)| scan(&index, source, parsed))
        .collect()
}

fn scan(
    index: &ClassAttributes<'_>,
    source: &SourceFile,
    parsed: &ParsedSource,
) -> Vec<StructureFinding> {
    let mut functions = Functions {
        owner: None,
        top: true,
        found: Vec::new(),
    };
    functions.visit_body(&parsed.module().body);
    let mut found = Vec::new();
    for (function, owner) in functions.found {
        let mut scope = Scope::new(index, source, parsed, function);
        if let Some(owner) = owner {
            scope.bind_receiver(function, owner);
        }
        scope.visit_body(&function.body);
        found.append(&mut scope.found);
    }
    found
}

/// Every function with its class, when it is a method of a top-level class.
struct Functions<'a> {
    owner: Option<&'a str>,
    top: bool,
    found: Vec<(&'a StmtFunctionDef, Option<&'a str>)>,
}

impl<'a> Visitor<'a> for Functions<'a> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        let saved = (self.owner, self.top);
        match statement {
            Stmt::FunctionDef(function) => {
                self.found.push((function, self.owner));
                self.owner = None;
                self.top = false;
            }
            Stmt::ClassDef(class) => {
                self.owner = self.top.then_some(class.name.as_str());
                self.top = false;
            }
            _ => {}
        }
        visitor::walk_stmt(self, statement);
        (self.owner, self.top) = saved;
    }
}

/// One function body, with the declared types of its unrebound names.
struct Scope<'a, 'i> {
    index: &'i ClassAttributes<'i>,
    source: &'a SourceFile,
    parsed: &'a ParsedSource,
    module: &'a str,
    writes: BindingCounts<'a>,
    /// Names that shadow a module binding inside the function.
    blocked: HashSet<&'a str>,
    types: HashMap<&'a str, Value>,
    found: Vec<StructureFinding>,
}

impl<'a, 'i> Scope<'a, 'i> {
    fn new(
        index: &'i ClassAttributes<'i>,
        source: &'a SourceFile,
        parsed: &'a ParsedSource,
        function: &'a StmtFunctionDef,
    ) -> Self {
        let writes = BindingCounts::direct_of_statements(&function.body);
        let type_parameters: HashSet<&str> = function
            .type_params
            .iter()
            .flat_map(|parameters| parameters.type_params.iter())
            .map(|parameter| parameter.name().as_str())
            .collect();
        let parameters = &function.parameters;
        let arguments: Vec<_> = parameters
            .posonlyargs
            .iter()
            .chain(&parameters.args)
            .chain(&parameters.kwonlyargs)
            .collect();
        let mut blocked: HashSet<&str> = writes
            .names()
            .chain(type_parameters.iter().copied())
            .collect();
        blocked.extend(
            arguments
                .iter()
                .map(|argument| argument.parameter.name.as_str()),
        );
        if let Some(variadic) = &parameters.vararg {
            blocked.insert(variadic.name.as_str());
        }
        if let Some(variadic) = &parameters.kwarg {
            blocked.insert(variadic.name.as_str());
        }
        let module = source.module.as_str();
        let mut types = HashMap::new();
        for argument in arguments {
            let name = argument.parameter.name.as_str();
            if writes.get(name) > 0 {
                continue;
            }
            if let Some(annotation) = &argument.parameter.annotation {
                types.insert(name, index.annotation(module, annotation, &type_parameters));
            }
        }
        Self {
            index,
            source,
            parsed,
            module,
            writes,
            blocked,
            types,
            found: Vec::new(),
        }
    }

    /// Type an unannotated method receiver as its class.
    fn bind_receiver(&mut self, function: &'a StmtFunctionDef, owner: &str) {
        let parameters = &function.parameters;
        let Some(receiver) = parameters.posonlyargs.iter().chain(&parameters.args).next() else {
            return;
        };
        let name = receiver.parameter.name.as_str();
        if receiver.parameter.annotation.is_some()
            || self.writes.get(name) > 0
            || CONSTRUCTION.contains(&function.name.as_str())
        {
            return;
        }
        let decorated = function.decorator_list.iter().all(|decorator| {
            self.index
                .qualified(self.module, &decorator.expression, &HashSet::new())
                .is_some_and(|name| RECEIVER_DECORATORS.contains(&name.as_str()))
        });
        if let (true, Some(class)) = (decorated, self.index.class_named(self.module, owner)) {
            self.types.insert(
                name,
                Value {
                    classes: Some(vec![class]),
                    elements: None,
                },
            );
        }
    }

    fn value(&self, expression: &Expr) -> Value {
        match expression {
            Expr::Name(name) => self
                .types
                .get(name.id.as_str())
                .cloned()
                .unwrap_or_default(),
            Expr::Attribute(attribute) => match self.value(&attribute.value).classes {
                Some(classes) if !classes.is_empty() => {
                    self.index.member(&classes, attribute.attr.as_str())
                }
                _ => Value::default(),
            },
            Expr::Call(call) => self
                .index
                .call(self.module, &call.func, false, &self.blocked),
            Expr::Await(awaited) => match awaited.value.as_ref() {
                Expr::Call(call) => self
                    .index
                    .call(self.module, &call.func, true, &self.blocked),
                _ => Value::default(),
            },
            _ => Value::default(),
        }
    }

    /// Record the type of a name the function binds exactly once.
    fn bind(&mut self, target: &'a Expr, value: Value) {
        if let Expr::Name(name) = target
            && self.writes.get(name.id.as_str()) == 1
        {
            self.types.insert(name.id.as_str(), value);
        }
    }

    fn reflection(&mut self, call: &ExprCall) {
        let Some(function) = self.index.qualified(self.module, &call.func, &self.blocked) else {
            return;
        };
        let arguments = &call.arguments;
        let accepted = match function.as_str() {
            "builtins.getattr" => [2, 3].contains(&arguments.args.len()),
            "builtins.hasattr" => arguments.args.len() == 2,
            _ => false,
        };
        if !accepted
            || !arguments.keywords.is_empty()
            || arguments.args.iter().any(Expr::is_starred_expr)
        {
            return;
        }
        let Expr::StringLiteral(literal) = &arguments.args[1] else {
            return;
        };
        let attribute = literal.value.to_str();
        // Private names are mangled in declarations but not in string arguments.
        if attribute.starts_with("__") {
            return;
        }
        let receiver = &arguments.args[0];
        let Some(classes) = self
            .value(receiver)
            .classes
            .filter(|classes| !classes.is_empty())
        else {
            return;
        };
        let mut owners = Vec::with_capacity(classes.len());
        for class in &classes {
            match self.index.lookup(class, attribute) {
                Lookup::Declared(owner) => owners.push(owner),
                Lookup::Missing | Lookup::Dynamic => return,
            }
        }
        let text = &self.parsed.text;
        let name = &text.text[receiver.range()];
        let short: Vec<&str> = classes
            .iter()
            .map(|class| class.rsplit('.').next().unwrap_or(class))
            .collect();
        let types = short.join(" | ");
        let declares = if classes.len() == 1 {
            "which declares"
        } else {
            "and each declares"
        };
        let hasattr = function == "builtins.hasattr";
        let (message, hint) = if hasattr {
            (
                format!("`{name}` is `{types}`, {declares} `{attribute}`; hasattr is always true"),
                format!(
                    "Remove the hasattr check and read {name}.{attribute} directly; review the branch it guarded, which always runs."
                ),
            )
        } else {
            (
                format!(
                    "`{name}` is `{types}`, {declares} `{attribute}`; read `{name}.{attribute}` directly"
                ),
                format!(
                    "Read {name}.{attribute} directly so the type checker sees the access; review checks on the result, such as isinstance, that its declared type already settles."
                ),
            )
        };
        let (line, column) = text.position(usize::from(call.start()));
        let (end_line, end_column) = text.position(usize::from(call.end()));
        self.found.push(StructureFinding {
            message,
            path: self.source.path.clone(),
            position: Some((line, column + 1)),
            end_line: Some(end_line),
            end_column: Some(end_column + 1),
            source_module: Some(self.source.module.clone()),
            expected: json!({
                "call": if hasattr { "hasattr" } else { "getattr" },
                "receiver": name,
                "attribute": attribute,
                "receiver_types": classes,
                "declared_by": owners,
                "default": arguments.args.len() == 3,
                "automatic_rewrite": false,
            }),
            hint,
            ..StructureFinding::default()
        });
    }
}

impl<'a> Visitor<'a> for Scope<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        match statement {
            // Nested scopes are scanned on their own, without outer names.
            Stmt::FunctionDef(_) | Stmt::ClassDef(_) => {}
            Stmt::For(node) => {
                let elements = self.value(&node.iter).elements;
                self.bind(
                    &node.target,
                    Value {
                        classes: elements,
                        elements: None,
                    },
                );
                visitor::walk_stmt(self, statement);
            }
            Stmt::Assign(node) => {
                visitor::walk_stmt(self, statement);
                if let [target] = &*node.targets {
                    let value = self.value(&node.value);
                    self.bind(target, value);
                }
            }
            Stmt::AnnAssign(node) => {
                visitor::walk_stmt(self, statement);
                let value = self
                    .index
                    .annotation(self.module, &node.annotation, &self.blocked);
                self.bind(&node.target, value);
            }
            _ => visitor::walk_stmt(self, statement),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Lambda(_)
            | Expr::ListComp(_)
            | Expr::SetComp(_)
            | Expr::DictComp(_)
            | Expr::Generator(_) => {}
            Expr::Call(call) => {
                self.reflection(call);
                visitor::walk_expr(self, expression);
            }
            _ => visitor::walk_expr(self, expression),
        }
    }
}
