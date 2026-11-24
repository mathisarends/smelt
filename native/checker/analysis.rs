//! Python semantics the ported rules share: name binding, `repr`, `ast.unparse`
//! and the declared Python version.
pub mod architecture;
pub mod bindings;
pub mod dump;
pub mod edits;
pub mod facade_usage;
pub mod imports;
pub mod installed;
mod lazy_exports;
pub mod members;
pub mod mirror;
pub mod module_access;
pub mod namespace;
pub mod nodes;
pub mod ordering;
mod printable;
pub mod private_attributes;
pub mod python_target;
pub mod repr;
pub mod similarity;
pub mod stdlib;
pub mod test_syntax;
pub mod unparse;
pub mod walk;
pub mod workspace;
