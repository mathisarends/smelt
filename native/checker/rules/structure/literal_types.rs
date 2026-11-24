//! TYP005: multi-value `Literal` types repeated within a root package.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::bindings::{Names, typing_bindings, typing_name};
use crate::checker::analysis::nodes::{parse_eval, subscript_parts};
use crate::checker::analysis::repr::{int_repr, json_string, str_repr};
use crate::checker::analysis::unparse::unparse;
use crate::checker::files::SourceFile;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::{
    ExceptHandler, Expr, ExprSubscript, Number, Operator, Stmt, StmtFunctionDef, UnaryOp,
};
use ruff_text_size::{Ranged, TextRange};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};

const MAX_LOCATIONS: usize = 5;

/// Each member's Python type name and `repr`, sorted.
type Members = Vec<(String, String)>;

struct Site {
    scope: String,
    line: usize,
    column: usize,
    end_line: usize,
    end_column: usize,
}

struct FileSites<'a> {
    module: &'a str,
    sites: Vec<Site>,
}

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let mut sources: Vec<&(&SourceFile, &ParsedSource)> = context.sources.iter().collect();
    sources.sort_by(|left, right| left.0.path.cmp(&right.0.path));
    let scanned: Vec<Vec<(Members, Site)>> = sources
        .par_iter()
        .map(|(_, parsed)| literal_types(parsed))
        .collect();
    let mut groups: BTreeMap<(&str, Members), BTreeMap<&str, FileSites<'_>>> = BTreeMap::new();
    for (entry, occurrences) in sources.iter().zip(scanned) {
        let source = entry.0;
        let module = source.module.as_str();
        let package = module.split('.').next().unwrap_or(module);
        for (members, site) in occurrences {
            groups
                .entry((package, members))
                .or_default()
                .entry(source.path.as_str())
                .or_insert_with(|| FileSites {
                    module,
                    sites: Vec::new(),
                })
                .sites
                .push(site);
        }
    }
    let mut found = Vec::new();
    for ((package, members), files) in &groups {
        let count: usize = files.values().map(|file| file.sites.len()).sum();
        if count >= 2 {
            report(package, members, files, count, &mut found);
        }
    }
    found
}

fn report(
    package: &str,
    members: &Members,
    files: &BTreeMap<&str, FileSites<'_>>,
    count: usize,
    found: &mut Vec<StructureFinding>,
) {
    let values: Vec<&str> = members.iter().map(|(_, value)| value.as_str()).collect();
    let label = format!("Literal[{}]", values.join(", "));
    let locations: Vec<Value> = files
        .iter()
        .flat_map(|(path, file)| {
            file.sites
                .iter()
                .map(move |site| json!({"path": path, "line": site.line, "column": site.column}))
        })
        .take(MAX_LOCATIONS)
        .collect();
    let listed: Vec<Value> = members
        .iter()
        .map(|(kind, value)| json!([kind, value]))
        .collect();
    for (path, file) in files {
        let first = &file.sites[0];
        let mut ordinals: HashMap<&str, usize> = HashMap::new();
        let edges: Vec<String> = file
            .sites
            .iter()
            .map(|site| {
                let ordinal = ordinals.entry(site.scope.as_str()).or_insert(0);
                let edge = literal_edge(members, &site.scope, *ordinal);
                *ordinal += 1;
                edge
            })
            .collect();
        found.push(StructureFinding {
            message: format!(
                "consider a shared alias for {label}; {count} occurrences in {package}"
            ),
            path: (*path).to_owned(),
            position: Some((first.line, first.column)),
            end_line: Some(first.end_line),
            end_column: Some(first.end_column),
            source_module: Some(file.module.to_owned()),
            expected: json!({
                "scope": package,
                "literal": label,
                "members": listed,
                "count": count,
                "locations": locations,
                "locations_truncated": count > locations.len(),
                "literal_edges": edges,
                "automatic_rewrite": false,
            }),
            hint: "Reuse or extract a plain type alias for the same concept; retain \
                   Annotated/Field metadata on the relevant wrappers."
                .to_owned(),
            ..StructureFinding::default()
        });
    }
}

/// `json.dumps([members, scope, ordinal])`.
fn literal_edge(members: &Members, scope: &str, ordinal: usize) -> String {
    let listed: Vec<String> = members
        .iter()
        .map(|(kind, value)| format!("[{}, {}]", json_string(kind), json_string(value)))
        .collect();
    format!(
        "[[{}], {}, {ordinal}]",
        listed.join(", "),
        json_string(scope)
    )
}

fn literal_types(parsed: &ParsedSource) -> Vec<(Members, Site)> {
    // Every supported direct import or access spells Literal in the source.
    if !parsed.text.text.contains("Literal") {
        return Vec::new();
    }
    let bindings = typing_bindings(parsed.module());
    if bindings.is_empty() {
        return Vec::new();
    }
    let mut collector = Collector {
        parsed,
        bindings: &bindings,
        scope: "module".to_owned(),
        found: Vec::new(),
    };
    collector.body(&parsed.module().body);
    collector.found
}

struct Collector<'a> {
    parsed: &'a ParsedSource,
    bindings: &'a Names<'a>,
    scope: String,
    found: Vec<(Members, Site)>,
}

impl Collector<'_> {
    fn body(&mut self, statements: &[Stmt]) {
        for statement in statements {
            self.statement(statement);
        }
    }

    fn statement(&mut self, statement: &Stmt) {
        match statement {
            Stmt::FunctionDef(node) => self.function(node),
            Stmt::ClassDef(node) => {
                let previous = self.enter(node.name.as_str());
                self.body(&node.body);
                self.scope = previous;
            }
            Stmt::AnnAssign(node) => {
                let target = unparse(&self.parsed.text.text, &node.target);
                self.annotation(&node.annotation, &format!("annotation:{target}"));
                if typing_name(&node.annotation, self.bindings) == Some("TypeAlias")
                    && let Some(value) = &node.value
                {
                    self.annotation(value, &format!("alias:{target}"));
                }
            }
            Stmt::TypeAlias(node) => {
                if let Expr::Name(name) = node.name.as_ref() {
                    self.annotation(&node.value, &format!("alias:{}", name.id));
                }
            }
            Stmt::Assign(node) => {
                if let ([Expr::Name(name)], Expr::Subscript(value)) =
                    (node.targets.as_slice(), node.value.as_ref())
                    && matches!(
                        typing_name(&value.value, self.bindings),
                        Some("Literal" | "Annotated")
                    )
                {
                    self.annotation(&node.value, &format!("alias:{}", name.id));
                }
            }
            Stmt::For(node) => {
                self.body(&node.body);
                self.body(&node.orelse);
            }
            Stmt::While(node) => {
                self.body(&node.body);
                self.body(&node.orelse);
            }
            Stmt::If(node) => {
                self.body(&node.body);
                for clause in &node.elif_else_clauses {
                    self.body(&clause.body);
                }
            }
            Stmt::With(node) => self.body(&node.body),
            Stmt::Match(node) => {
                for case in &node.cases {
                    self.body(&case.body);
                }
            }
            Stmt::Try(node) => {
                self.body(&node.body);
                for handler in &node.handlers {
                    let ExceptHandler::ExceptHandler(handler) = handler;
                    self.body(&handler.body);
                }
                self.body(&node.orelse);
                self.body(&node.finalbody);
            }
            _ => {}
        }
    }

    fn enter(&mut self, name: &str) -> String {
        let nested = format!("{}.{name}", self.scope);
        std::mem::replace(&mut self.scope, nested)
    }

    fn function(&mut self, node: &StmtFunctionDef) {
        let previous = self.enter(node.name.as_str());
        let parameters = &node.parameters;
        let arguments = parameters
            .posonlyargs
            .iter()
            .chain(&parameters.args)
            .chain(&parameters.kwonlyargs)
            .map(|item| &item.parameter)
            .chain(parameters.vararg.as_deref())
            .chain(parameters.kwarg.as_deref());
        for argument in arguments {
            if let Some(annotation) = argument.annotation() {
                self.annotation(annotation, &format!("parameter:{}", argument.name));
            }
        }
        if let Some(returns) = &node.returns {
            self.annotation(returns, "return");
        }
        self.body(&node.body);
        self.scope = previous;
    }

    fn annotation(&mut self, root: &Expr, role: &str) {
        let mut literals = Vec::new();
        annotation_literals(root, self.bindings, None, &mut literals);
        let text = &self.parsed.text;
        for (range, members) in literals {
            let (line, column) = text.position(usize::from(range.start()));
            let (end_line, end_column) = text.position(usize::from(range.end()));
            self.found.push((
                members,
                Site {
                    scope: format!("{}:{role}", self.scope),
                    line,
                    column: column + 1,
                    end_line,
                    end_column: end_column + 1,
                },
            ));
        }
    }
}

/// The `Literal` types of an annotation; `quoted` is the string it was parsed from,
/// since a quoted annotation's diagnostic points at its real source range.
fn annotation_literals(
    node: &Expr,
    bindings: &Names<'_>,
    quoted: Option<TextRange>,
    found: &mut Vec<(TextRange, Members)>,
) {
    match node {
        Expr::StringLiteral(item) => {
            if let Some(parsed) = parse_eval(item.value.to_str()) {
                let range = quoted.unwrap_or_else(|| item.range());
                annotation_literals(parsed.expr(), bindings, Some(range), found);
            }
        }
        Expr::Subscript(item) => {
            let name = typing_name(&item.value, bindings);
            if name == Some("Literal") {
                if let Some(members) = literal_members(item, bindings)
                    && members.len() >= 2
                {
                    found.push((quoted.unwrap_or_else(|| item.range()), members));
                }
                return;
            }
            let parts = subscript_parts(&item.slice);
            let parts = if name == Some("Annotated") {
                &parts[..parts.len().min(1)]
            } else {
                parts
            };
            for part in parts {
                annotation_literals(part, bindings, quoted, found);
            }
        }
        Expr::BinOp(item) if item.op == Operator::BitOr => {
            annotation_literals(&item.left, bindings, quoted, found);
            annotation_literals(&item.right, bindings, quoted, found);
        }
        Expr::List(item) => {
            for part in &item.elts {
                annotation_literals(part, bindings, quoted, found);
            }
        }
        Expr::Tuple(item) => {
            for part in &item.elts {
                annotation_literals(part, bindings, quoted, found);
            }
        }
        _ => {}
    }
}

fn literal_members(node: &ExprSubscript, bindings: &Names<'_>) -> Option<Members> {
    let mut members: BTreeSet<(String, String)> = BTreeSet::new();
    for part in subscript_parts(&node.slice) {
        match part {
            Expr::Subscript(nested) if typing_name(&nested.value, bindings) == Some("Literal") => {
                members.extend(literal_members(nested, bindings)?);
            }
            Expr::StringLiteral(item) => {
                members.insert(("str".to_owned(), str_repr(item.value.to_str())));
            }
            Expr::BooleanLiteral(item) => {
                let value = if item.value { "True" } else { "False" };
                members.insert(("bool".to_owned(), value.to_owned()));
            }
            Expr::NoneLiteral(_) => {
                members.insert(("NoneType".to_owned(), "None".to_owned()));
            }
            Expr::NumberLiteral(item) => {
                let Number::Int(value) = &item.value else {
                    return None;
                };
                members.insert(("int".to_owned(), int_repr(value)));
            }
            Expr::UnaryOp(item) if matches!(item.op, UnaryOp::USub | UnaryOp::UAdd) => {
                let Expr::NumberLiteral(operand) = item.operand.as_ref() else {
                    return None;
                };
                let Number::Int(value) = &operand.value else {
                    return None;
                };
                let digits = int_repr(value);
                let text = if item.op == UnaryOp::USub && digits != "0" {
                    format!("-{digits}")
                } else {
                    digits
                };
                members.insert(("int".to_owned(), text));
            }
            // Names, enum members and calls need semantic resolution, not eval.
            _ => return None,
        }
    }
    Some(members.into_iter().collect())
}
