//! TST012: modules with behaviour but no test at their mirrored path.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::mirror::Mirroring;
use crate::checker::analysis::nodes::is_constant;
use crate::checker::analysis::walk::{TreeNode, tree_nodes};
use crate::checker::config::paths::fnmatchcase;
use ruff_python_ast::{ModModule, Stmt};
use serde_json::json;

pub fn check(context: &StructureContext<'_>) -> Vec<StructureFinding> {
    let policy = context.test_policy;
    if policy.layout != "mirror" {
        return Vec::new();
    }
    let mirroring = Mirroring::new(context.files, policy, &context.project.root_packages);
    let tests = &context.files.tests;
    let mut found = Vec::new();
    for (source, parsed) in context.sources {
        let module = source.module.as_str();
        if source.is_package || module.rsplit('.').next() == Some("__main__") {
            continue;
        }
        let Some(expected) = mirror_path(context, &mirroring, module) else {
            continue;
        };
        if tests.contains_key(&expected) {
            continue;
        }
        if policy.mirror_suffixes
            && mirror_path(context, &mirroring, &format!("{module}_*"))
                .is_some_and(|pattern| tests.keys().any(|path| fnmatchcase(&pattern, path)))
        {
            continue;
        }
        if !has_behaviour(parsed.module()) {
            continue;
        }
        let info = context.model.info(module);
        found.push(StructureFinding {
            message: format!("{module} has no test at its mirrored path {expected}"),
            path: source.path.clone(),
            position: Some((1, 0)),
            column_less: true,
            feature: info.as_ref().and_then(|info| info.feature.clone()),
            layer: info.as_ref().and_then(|info| info.layer.clone()),
            expected: json!({"test_path": expected}),
            hint: format!(
                "Add {expected} with tests of the module's behaviour, or keep a reasoned \
                 suppression when other tests deliberately cover it."
            ),
            ..StructureFinding::default()
        });
    }
    found
}

/// The mirrored test path of `module` under the first test root of its package.
fn mirror_path(
    context: &StructureContext<'_>,
    mirroring: &Mirroring<'_>,
    module: &str,
) -> Option<String> {
    let root = module.split('.').next().unwrap_or(module);
    context.project.test_roots.iter().find_map(|test_root| {
        let test_root = test_root.trim_matches('/');
        mirroring
            .member_roots(test_root)
            .iter()
            .any(|member| member == root)
            .then(|| mirroring.target(test_root, module))
    })
}

/// Whether the module defines a function or method with a real body.
fn has_behaviour(module: &ModModule) -> bool {
    tree_nodes(module).into_iter().any(|node| match node {
        TreeNode::Statement(Stmt::FunctionDef(function)) => !is_stub(&function.body),
        _ => false,
    })
}

/// A body without behaviour: a docstring or other constant, `pass` or a raise.
fn is_stub(body: &[Stmt]) -> bool {
    body.iter().all(|statement| match statement {
        Stmt::Pass(_) | Stmt::Raise(_) => true,
        Stmt::Expr(expression) => is_constant(&expression.value),
        _ => false,
    })
}
