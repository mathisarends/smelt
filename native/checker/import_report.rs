//! The resolved import graph as JSON, which the parity comparison reads.
use super::analysis::dump::sha256_hex;
use super::analysis::imports::{ImportDetail, ImportIndex};
use super::config::Request;
use super::files::{FileIndex, SourceFile};
use super::source::ParsedSource;
use super::with_analysis_pool;
use rayon::prelude::*;
use serde_json::{Map, Value, json};
use std::path::Path;

pub fn run(config: &Path) -> Result<String, String> {
    let request = Request::load(config, Vec::new())?;
    let files = FileIndex::discover(&request)?;
    let sources: Vec<&SourceFile> = files.sources.values().collect();
    let tests: Vec<&SourceFile> = files.tests.values().collect();
    with_analysis_pool(|| {
        let parsed_sources = parse(&sources)?;
        let parsed_tests = parse(&tests)?;
        let source_pairs: Vec<_> = sources.iter().copied().zip(&parsed_sources).collect();
        let test_pairs: Vec<_> = tests.iter().copied().zip(&parsed_tests).collect();
        let index = ImportIndex::build(
            &files,
            &request.project.root_packages,
            &source_pairs,
            &test_pairs,
        )?;
        Ok(report(&index, &request.project.root_packages).to_string())
    })?
}

fn parse(files: &[&SourceFile]) -> Result<Vec<ParsedSource>, String> {
    files
        .par_iter()
        .map(|file| ParsedSource::read(file))
        .collect()
}

fn report(index: &ImportIndex, roots: &[String]) -> Value {
    let details: Vec<Value> = index.all_imports().map(detail_json).collect();
    let tests: Vec<Value> = index
        .imports_in_tests()
        .iter()
        .map(|(path, detail)| json!([path, detail_json(detail)]))
        .collect();
    let first_root = roots.first().map(std::slice::from_ref);
    let mut chains = Map::new();
    for module in index.modules() {
        for include in [true, false] {
            for (label, scope) in [("all", None), ("first", first_root)] {
                let paths = index.shortest_chains_from(module, include, scope);
                let text = paths
                    .iter()
                    .map(|path| {
                        path.iter()
                            .map(|hop| {
                                format!(
                                    "{}>{}:{}:{}",
                                    hop.importer, hop.imported, hop.line, hop.column
                                )
                            })
                            .collect::<Vec<_>>()
                            .join("|")
                    })
                    .collect::<Vec<_>>()
                    .join("\n");
                chains.insert(
                    format!("{module}|{include}|{label}"),
                    json!([paths.len(), sha256_hex(&text)]),
                );
            }
        }
    }
    json!({"details": details, "tests": tests, "chains": chains})
}

fn detail_json(detail: &ImportDetail) -> Value {
    json!({
        "importer": detail.importer,
        "imported": detail.imported,
        "line": detail.line,
        "line_contents": detail.line_contents,
        "names": detail.names,
        "type_checking": detail.type_checking,
        "external": detail.external,
        "column": detail.column,
        "end_column": detail.end_column,
    })
}
