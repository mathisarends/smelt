//! MOD012: `modeling/parallel-record-fields`.
use super::{Finding, RuleInput};
use crate::checker::analysis::bindings::{
    BindingCounts, annotation_name, qualified_name, unshadowed_bindings,
};
use crate::checker::analysis::nodes::walk_statements;
use crate::checker::violation::Violation;
use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{
    Expr, ExprCall, ExprContext, Stmt, StmtAnnAssign, StmtClassDef, StmtFunctionDef,
};
use ruff_text_size::Ranged;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet, HashMap};

const SEQUENCES: [&str; 8] = [
    "builtins.list",
    "builtins.tuple",
    "typing.List",
    "typing.Tuple",
    "typing.Sequence",
    "typing.MutableSequence",
    "collections.abc.Sequence",
    "collections.abc.MutableSequence",
];
const MIN_FIELDS: usize = 2;
const HINT: &str = "Make the record relationship explicit, or document intentional column storage.";

type Bindings = HashMap<String, String>;

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let source = input.parsed.text.text.as_str();
    let bindings = unshadowed_bindings(source, module, &["zip", "list", "tuple"]);
    let mut found = Vec::new();
    for statement in walk_statements(module) {
        let Stmt::ClassDef(owner) = statement else {
            continue;
        };
        let fields = declared_fields(owner, &bindings);
        for (names, mut sites) in zip_groups(input, owner, &fields, &bindings) {
            sites.sort_by_key(|site| site.start());
            let (line, column) = input.place(sites[0].start());
            let joined = names.join(", ");
            let declarations: Vec<_> = names
                .iter()
                .map(|name| {
                    let (line, column) = input.place(fields[name].start());
                    json!({"field": name, "line": line, "column": column})
                })
                .collect();
            let zip_sites: Vec<_> = sites
                .iter()
                .map(|site| {
                    let (line, column) = input.place(site.start());
                    json!({"line": line, "column": column})
                })
                .collect();
            found.push(input.violation(Finding {
                message: format!(
                    "Stored collections {joined} on {} are paired by position; consider one collection of named records",
                    owner.name
                ),
                line,
                column,
                end_line: None,
                end_column: None,
                expected: json!({
                    "class": owner.name.as_str(),
                    "fields": names,
                    "pairing_edges": [format!("{}: {joined}", owner.name)],
                    "declarations": declarations,
                    "zip_sites": zip_sites,
                    "automatic_rewrite": false,
                }),
                hint: HINT.to_owned(),
                fix: None,
            }));
        }
    }
    found
}

fn declared_fields<'a>(
    owner: &'a StmtClassDef,
    bindings: &Bindings,
) -> HashMap<&'a str, &'a StmtAnnAssign> {
    let declarations = owner
        .body
        .iter()
        .filter(|node| !matches!(node, Stmt::FunctionDef(_) | Stmt::ClassDef(_)));
    let counts = BindingCounts::of_statements(declarations);
    let hidden: Vec<&str> = owner
        .body
        .iter()
        .filter_map(|node| match node {
            Stmt::FunctionDef(item) => Some(item.name.as_str()),
            Stmt::ClassDef(item) => Some(item.name.as_str()),
            _ => None,
        })
        .collect();
    let mut fields = HashMap::new();
    for statement in &owner.body {
        let Stmt::AnnAssign(field) = statement else {
            continue;
        };
        let Expr::Name(target) = field.target.as_ref() else {
            continue;
        };
        let name = target.id.as_str();
        if counts.get(name) == 1
            && !hidden.contains(&name)
            && annotation_name(&field.annotation, bindings)
                .is_some_and(|outer| SEQUENCES.contains(&outer.as_str()))
        {
            fields.insert(name, field);
        }
    }
    fields
}

fn zip_groups<'a>(
    input: &RuleInput<'_>,
    owner: &'a StmtClassDef,
    fields: &HashMap<&'a str, &'a StmtAnnAssign>,
    bindings: &Bindings,
) -> BTreeMap<Vec<&'a str>, Vec<&'a ExprCall>> {
    let mut groups: BTreeMap<Vec<&'a str>, Vec<&'a ExprCall>> = BTreeMap::new();
    for statement in &owner.body {
        let Stmt::FunctionDef(method) = statement else {
            continue;
        };
        if !method.decorator_list.is_empty() {
            continue;
        }
        let parameters = &method.parameters;
        let Some(first) = parameters.posonlyargs.iter().chain(&parameters.args).next() else {
            continue;
        };
        let receiver = first.parameter.name.as_str();
        let mut nodes = MethodNodes {
            receiver,
            rebound: false,
            calls: Vec::new(),
        };
        nodes.visit_body(&method.body);
        if nodes.rebound {
            continue;
        }
        let counts = BindingCounts::of_statements([statement]);
        for call in nodes.calls {
            if qualified_name(&call.func, bindings).as_deref() != Some("builtins.zip") {
                continue;
            }
            let (before, _) = input.place(call.start());
            let aliases = local_aliases(input, method, receiver, before, &counts);
            let names: Option<BTreeSet<&'a str>> = call
                .arguments
                .args
                .iter()
                .map(|argument| {
                    let name = field_name(argument, receiver, &aliases)?;
                    fields.get_key_value(name).map(|(key, _)| *key)
                })
                .collect();
            let Some(names) = names else {
                continue;
            };
            if names.len() >= MIN_FIELDS {
                groups
                    .entry(names.into_iter().collect())
                    .or_default()
                    .push(call);
            }
        }
    }
    groups
}

fn local_aliases<'a>(
    input: &RuleInput<'_>,
    method: &'a StmtFunctionDef,
    receiver: &str,
    before: usize,
    counts: &BindingCounts<'_>,
) -> HashMap<&'a str, &'a str> {
    let mut aliases = HashMap::new();
    for statement in &method.body {
        let (target, value): (&Expr, &Expr) = match statement {
            Stmt::Assign(node) if node.targets.len() == 1 => (&node.targets[0], &node.value),
            Stmt::AnnAssign(node) => match &node.value {
                Some(value) => (&node.target, value),
                None => continue,
            },
            _ => continue,
        };
        if input.place(statement.start()).0 >= before {
            continue;
        }
        if let Expr::Name(target) = target
            && counts.get(target.id.as_str()) == 1
            && let Some(name) = field_name(value, receiver, &HashMap::new())
        {
            aliases.insert(target.id.as_str(), name);
        }
    }
    aliases
}

fn field_name<'a>(
    node: &'a Expr,
    receiver: &str,
    aliases: &HashMap<&str, &'a str>,
) -> Option<&'a str> {
    match node {
        Expr::Attribute(item) if matches!(item.value.as_ref(), Expr::Name(owner) if owner.id.as_str() == receiver) => {
            Some(item.attr.as_str())
        }
        Expr::Name(item) => aliases.get(item.id.as_str()).copied(),
        _ => None,
    }
}

/// Calls of a method body, outside nested functions, classes and lambdas.
struct MethodNodes<'a, 'b> {
    receiver: &'b str,
    rebound: bool,
    calls: Vec<&'a ExprCall>,
}

impl<'a> Visitor<'a> for MethodNodes<'a, '_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if !matches!(statement, Stmt::FunctionDef(_) | Stmt::ClassDef(_)) {
            visitor::walk_stmt(self, statement);
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        match expression {
            Expr::Lambda(_) => return,
            Expr::Name(node)
                if node.id.as_str() == self.receiver && node.ctx != ExprContext::Load =>
            {
                self.rebound = true;
            }
            Expr::Call(node) => self.calls.push(node),
            _ => {}
        }
        visitor::walk_expr(self, expression);
    }
}
