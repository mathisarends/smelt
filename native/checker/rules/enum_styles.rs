//! MOD004: `modeling/str-enum-value-convention`.
use super::{Finding, RuleInput};
use crate::checker::analysis::edits::{Skipped, add_from_import, fix, name_used, remove_aliases};
use crate::checker::analysis::nodes::walk_statements;
use crate::checker::source::ParsedSource;
use crate::checker::violation::Violation;
use ruff_python_ast::{Alias, Expr, ModModule, Stmt, StmtClassDef};
use ruff_text_size::Ranged;
use serde_json::{Value, json};
use std::collections::{BTreeSet, HashMap, HashSet};

const DEFAULT_STYLE: &str = "explicit";

pub fn check(input: &RuleInput<'_>) -> Vec<Violation> {
    let module = input.parsed.module();
    let names = EnumNames::new(module);
    let policy = input
        .contracts
        .policy_for(&input.source.module, "str_enum_values");
    let style = policy
        .as_ref()
        .and_then(|origin| origin.value.as_str())
        .filter(|value| !value.is_empty())
        .unwrap_or(DEFAULT_STYLE);
    let mut found = Vec::new();
    for statement in walk_statements(module) {
        let Stmt::ClassDef(node) = statement else {
            continue;
        };
        if !names.is_str_enum(node) {
            continue;
        }
        let members = Members::of(node, &names);
        let custom = members
            .literal
            .iter()
            .any(|(name, value)| *value != name.to_lowercase());
        if (style == "explicit" || custom) && !members.automatic.is_empty() {
            let message = if style == "explicit" {
                format!("StrEnum {} derives values with auto()", node.name)
            } else {
                format!(
                    "StrEnum {} has custom values; spell its auto() members explicitly",
                    node.name
                )
            };
            let listed: Vec<&str> = members.automatic.clone();
            let hint = spell(&listed);
            let repair = explicit_fix(input.parsed, node, &members);
            found.push(report(
                input,
                node,
                (message, hint),
                (listed, style),
                repair,
            ));
        } else if style == "auto" && !custom && !members.literal.is_empty() {
            let ordered: Vec<&str> = members.literal.iter().map(|(name, _)| *name).collect();
            let hint = format!("Use auto() for {}.", ordered.join(", "));
            let sorted: Vec<&str> = ordered
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            let repair = auto_fix(input.parsed, node, &members, &names);
            let message = format!("StrEnum {} spells values that auto() derives", node.name);
            found.push(report(
                input,
                node,
                (message, hint),
                (sorted, style),
                repair,
            ));
        }
    }
    found
}

fn report(
    input: &RuleInput<'_>,
    node: &StmtClassDef,
    (message, hint): (String, String),
    (members, style): (Vec<&str>, &str),
    repair: Option<Value>,
) -> Violation {
    let (line, column) = input.place(input.parsed.class_start(node));
    input.violation(Finding {
        message,
        line,
        column,
        end_line: None,
        end_column: None,
        expected: json!({
            "enum": node.name.as_str(),
            "members": members,
            "str_enum_values": style,
            "automatic_rewrite": repair.is_some(),
        }),
        hint,
        fix: repair,
    })
}

fn spell(members: &[&str]) -> String {
    let listed: Vec<String> = members
        .iter()
        .map(|member| format!("{member} = \"{}\"", member.to_lowercase()))
        .collect();
    format!("Spell {} explicitly.", listed.join(", "))
}

/// Local names that refer to `enum.StrEnum` and `enum.auto` in one module.
struct EnumNames<'a> {
    modules: HashSet<&'a str>,
    str_enum: HashSet<&'a str>,
    auto: HashSet<&'a str>,
}

impl<'a> EnumNames<'a> {
    fn new(module: &'a ModModule) -> Self {
        let mut names = Self {
            modules: HashSet::new(),
            str_enum: HashSet::new(),
            auto: HashSet::new(),
        };
        for statement in &module.body {
            match statement {
                Stmt::Import(node) => {
                    for alias in &node.names {
                        if alias.name.as_str() == "enum" {
                            names.modules.insert(local_name(alias));
                        }
                    }
                }
                Stmt::ImportFrom(node)
                    if node
                        .module
                        .as_ref()
                        .is_some_and(|name| name.as_str() == "enum") =>
                {
                    for alias in &node.names {
                        match alias.name.as_str() {
                            "StrEnum" => {
                                names.str_enum.insert(local_name(alias));
                            }
                            "auto" => {
                                names.auto.insert(local_name(alias));
                            }
                            _ => {}
                        }
                    }
                }
                _ => {}
            }
        }
        names
    }

    fn is_str_enum(&self, node: &StmtClassDef) -> bool {
        matches!(node.bases(), [base] if self.refers(base, &self.str_enum, "StrEnum"))
    }

    /// How this module names `enum.auto`, or `None` without an import for it.
    fn auto_spelling(&self) -> Option<String> {
        if let Some(name) = self.auto.iter().min() {
            return Some((*name).to_owned());
        }
        self.modules.iter().min().map(|name| format!("{name}.auto"))
    }

    fn is_auto(&self, value: &Expr) -> bool {
        matches!(value, Expr::Call(call)
            if call.arguments.args.is_empty()
                && call.arguments.keywords.is_empty()
                && self.refers(&call.func, &self.auto, "auto"))
    }

    fn refers(&self, node: &Expr, names: &HashSet<&str>, attribute: &str) -> bool {
        match node {
            Expr::Name(item) => names.contains(item.id.as_str()),
            Expr::Attribute(item) => {
                item.attr.as_str() == attribute
                    && matches!(item.value.as_ref(), Expr::Name(owner)
                        if self.modules.contains(owner.id.as_str()))
            }
            _ => false,
        }
    }
}

fn local_name(alias: &Alias) -> &str {
    alias.asname.as_ref().unwrap_or(&alias.name).as_str()
}

/// The string and `auto()` members of a class, in declaration order.
struct Members<'a> {
    automatic: Vec<&'a str>,
    literal: Vec<(&'a str, &'a str)>,
    values: HashMap<&'a str, &'a Expr>,
}

impl<'a> Members<'a> {
    fn of(node: &'a StmtClassDef, names: &EnumNames<'_>) -> Self {
        let mut members = Self {
            automatic: Vec::new(),
            literal: Vec::new(),
            values: HashMap::new(),
        };
        for child in &node.body {
            let (target, value) = match child {
                Stmt::Assign(item) if item.targets.len() == 1 => {
                    (&item.targets[0], Some(item.value.as_ref()))
                }
                Stmt::AnnAssign(item) => (item.target.as_ref(), item.value.as_deref()),
                _ => continue,
            };
            let (Expr::Name(target), Some(value)) = (target, value) else {
                continue;
            };
            let name = target.id.as_str();
            if name.starts_with('_') {
                continue;
            }
            if names.is_auto(value) {
                members.automatic.push(name);
            } else if let Expr::StringLiteral(text) = value {
                let spelled = text.value.to_str();
                match members.literal.iter_mut().find(|(item, _)| *item == name) {
                    Some(entry) => entry.1 = spelled,
                    None => members.literal.push((name, spelled)),
                }
            } else {
                continue;
            }
            members.values.insert(name, value);
        }
        members
    }
}

fn generates_values(node: &StmtClassDef) -> bool {
    node.body.iter().any(|child| match child {
        Stmt::FunctionDef(item) => !item.is_async && item.name.as_str() == "_generate_next_value_",
        Stmt::Assign(item) => item.targets.iter().any(
            |target| matches!(target, Expr::Name(name) if name.id.as_str() == "_generate_next_value_"),
        ),
        _ => false,
    })
}

/// Spell each `auto()` member as its lowercased name.
///
/// Safe: that is the value `auto()` produces for a `StrEnum`, unless the class
/// overrides `_generate_next_value_`.
fn explicit_fix(
    parsed: &ParsedSource,
    node: &StmtClassDef,
    members: &Members<'_>,
) -> Option<Value> {
    if generates_values(node) {
        return None;
    }
    let text = &parsed.text;
    let calls: Vec<&Expr> = members
        .automatic
        .iter()
        .map(|name| members.values[name])
        .collect();
    let mut edits: Vec<Value> = members
        .automatic
        .iter()
        .zip(&calls)
        .map(|(name, call)| text.replace(call.range(), &format!("\"{}\"", name.to_lowercase())))
        .collect();
    let ignored: Vec<Skipped<'_>> = calls.iter().map(|call| Skipped::Expression(call)).collect();
    for statement in &parsed.module().body {
        let Stmt::ImportFrom(import) = statement else {
            continue;
        };
        if import
            .module
            .as_ref()
            .is_none_or(|name| name.as_str() != "enum")
        {
            continue;
        }
        let unused: Vec<&Alias> = import
            .names
            .iter()
            .filter(|alias| {
                alias.name.as_str() == "auto" && !name_used(parsed, local_name(alias), &ignored)
            })
            .collect();
        if !unused.is_empty()
            && let Some(edit) = remove_aliases(text, statement, &unused)
        {
            edits.push(edit);
        }
    }
    Some(fix(
        "safe",
        &format!("Spell the values of {} explicitly", node.name),
        edits,
    ))
}

/// Replace explicit values that equal the lowercased name with `auto()`.
///
/// Safe: `auto()` derives the same value, unless the class overrides
/// `_generate_next_value_`.
fn auto_fix(
    parsed: &ParsedSource,
    node: &StmtClassDef,
    members: &Members<'_>,
    names: &EnumNames<'_>,
) -> Option<Value> {
    if generates_values(node) {
        return None;
    }
    let text = &parsed.text;
    let mut edits = Vec::new();
    let spelling = match names.auto_spelling() {
        Some(spelling) => spelling,
        None => {
            if name_used(parsed, "auto", &[]) {
                return None;
            }
            edits.push(add_from_import(parsed, "enum", "auto"));
            "auto".to_owned()
        }
    };
    edits.extend(
        members
            .literal
            .iter()
            .map(|(name, _)| text.replace(members.values[name].range(), &format!("{spelling}()"))),
    );
    Some(fix(
        "safe",
        &format!("Derive the values of {} with auto()", node.name),
        edits,
    ))
}
