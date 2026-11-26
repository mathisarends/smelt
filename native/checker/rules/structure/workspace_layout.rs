//! LAY013: top-level packages outside the workspace's canonical layout.
use super::{StructureContext, StructureFinding};
use crate::checker::analysis::workspace::{
    canonicalize_name, member_for_path, member_paths, root_project_name,
};
use serde_json::json;

pub fn check(context: &StructureContext<'_>) -> Result<Vec<StructureFinding>, String> {
    let project = context.project;
    let Some(layout) = project
        .workspace_layout
        .as_deref()
        .filter(|_| project.uv_workspace)
    else {
        return Ok(Vec::new());
    };

    let source = if layout == "src" { "src/" } else { "" };
    let files = context.files;
    let members = member_paths(files)?;
    let root_name = root_project_name(files)?;
    let mut found = Vec::new();
    for package in &project.root_packages {
        for directory in files.package_parts(package) {
            let member = member_for_path(&members, directory);
            let (prefix, member_name) = if member == "." {
                let Some(name) = root_name.as_deref() else {
                    continue;
                };
                (String::new(), name)
            } else if directory == member {
                // A member directory is not a package of its parent project.
                continue;
            } else {
                (
                    format!("{member}/"),
                    member.rsplit('/').next().unwrap_or(member),
                )
            };
            let expected_package = canonicalize_name(member_name).replace('-', "_");
            let expected = format!("{prefix}{source}{expected_package}");
            if *directory == expected {
                continue;
            }
            let path = files
                .path_for_module(package)
                .filter(|path| path.strip_suffix("/__init__.py") == Some(directory.as_str()))
                .unwrap_or(directory);
            found.push(StructureFinding {
                message: format!(
                    "workspace package directory \"{directory}\" must be \"{expected}\""
                ),
                path: path.to_owned(),
                source_module: Some(package.clone()),
                expected: json!({"path": expected, "layout": layout}),
                hint: format!(
                    "Use \"{expected}\" for the package and update affected build settings, \
                     Smelt paths and imports."
                ),
                ..StructureFinding::default()
            });
        }
    }
    Ok(found)
}
