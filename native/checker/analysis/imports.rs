//! The project's import graph, with the module resolution the Python engine uses.
use super::namespace::resolve_relative;
use super::walk::{TreeNode, tree_nodes};
use crate::checker::files::{FileIndex, SourceFile};
use crate::checker::patterns::is_within;
use crate::checker::source::ParsedSource;
use rayon::prelude::*;
use ruff_python_ast::{Alias, Expr, Stmt, StmtImport, StmtImportFrom};
use ruff_text_size::{Ranged, TextSize};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};

#[derive(Clone)]
pub struct ImportDetail {
    pub importer: String,
    /// The resolved target; external packages are reduced to their top level.
    pub imported: String,
    pub line: usize,
    pub line_contents: String,
    /// Fully qualified names the statement imports.
    pub names: Vec<String>,
    pub type_checking: bool,
    pub external: bool,
    pub column: usize,
    pub end_column: usize,
}

impl ImportDetail {
    pub fn top_level(&self) -> &str {
        self.imported.split('.').next().unwrap_or_default()
    }
}

pub struct ImportIndex {
    modules: Vec<String>,
    outgoing: HashMap<String, Vec<ImportDetail>>,
    tests: Vec<(String, ImportDetail)>,
}

impl ImportIndex {
    /// Resolve the imports of every source and test file.
    pub fn build(
        files: &FileIndex,
        roots: &[String],
        sources: &[(&SourceFile, &ParsedSource)],
        tests: &[(&SourceFile, &ParsedSource)],
    ) -> Result<Self, String> {
        if files.found_roots.is_empty() {
            return Err(
                "none of project.root_packages were found under project.source_roots".to_owned(),
            );
        }
        let resolver = Resolver::new(files, roots);
        let outgoing: HashMap<String, Vec<ImportDetail>> = sources
            .par_iter()
            .map(|(source, parsed)| (source.module.clone(), resolver.details(source, parsed)))
            .collect();
        let known: HashSet<&str> = files
            .sources
            .keys()
            .chain(&files.packages)
            .map(String::as_str)
            .collect();
        let tests = tests
            .iter()
            .flat_map(|(test, parsed)| {
                test_details(test, parsed, &known)
                    .into_iter()
                    .map(|detail| (test.path.clone(), detail))
            })
            .collect();
        Ok(Self {
            modules: files.sources.keys().cloned().collect(),
            outgoing,
            tests,
        })
    }

    /// Analyzed modules in lexical order.
    pub fn modules(&self) -> &[String] {
        &self.modules
    }

    pub fn imports_of(&self, module: &str) -> &[ImportDetail] {
        self.outgoing.get(module).map_or(&[], Vec::as_slice)
    }

    pub fn all_imports(&self) -> impl Iterator<Item = &ImportDetail> {
        self.modules
            .iter()
            .flat_map(|module| self.imports_of(module))
    }

    /// Project imports in test files, by path.
    pub fn imports_in_tests(&self) -> &[(String, ImportDetail)] {
        &self.tests
    }

    /// Shortest import paths, including external leaves, without revisiting cycles.
    ///
    /// Modules are traversed in lexical order and the earliest active import wins,
    /// so every run reports the same witness.
    pub fn shortest_chains_from(
        &self,
        module: &str,
        include_type_checking: bool,
        first_party_roots: Option<&[String]>,
    ) -> Vec<Vec<&ImportDetail>> {
        let mut chains = Vec::new();
        let mut previous: HashMap<&str, &ImportDetail> = HashMap::new();
        let mut visited: HashSet<&str> = HashSet::from([module]);
        let mut queue: VecDeque<&str> = VecDeque::from([module]);
        while let Some(current) = queue.pop_front() {
            let mut details: Vec<&ImportDetail> = self.imports_of(current).iter().collect();
            details.sort_by_key(|detail| (&detail.imported, detail.line, detail.column));
            for detail in details {
                if detail.type_checking && !include_type_checking {
                    continue;
                }
                if !detail.external {
                    if first_party_roots
                        .is_some_and(|roots| !roots.iter().any(|r| r == detail.top_level()))
                    {
                        continue;
                    }
                    if !visited.insert(detail.imported.as_str()) {
                        continue;
                    }
                    previous.insert(detail.imported.as_str(), detail);
                    queue.push_back(detail.imported.as_str());
                }
                let mut path = vec![detail];
                while let Some(last) = path.last()
                    && last.importer != module
                {
                    path.push(previous[last.importer.as_str()]);
                }
                path.reverse();
                chains.push(path);
            }
        }
        chains
    }
}

struct Resolver<'a> {
    /// Modules with a file, and namespace packages.
    internal: HashSet<&'a str>,
    found: Vec<&'a str>,
    roots: &'a [String],
    files: &'a FileIndex,
}

impl<'a> Resolver<'a> {
    fn new(files: &'a FileIndex, roots: &'a [String]) -> Self {
        let internal = files
            .sources
            .keys()
            .chain(&files.namespace_packages)
            .map(String::as_str)
            .collect();
        Self {
            internal,
            found: files.found_roots.iter().map(String::as_str).collect(),
            roots,
            files,
        }
    }

    fn details(&self, source: &SourceFile, parsed: &ParsedSource) -> Vec<ImportDetail> {
        let module = source.module.as_str();
        let statements = import_statements(parsed);
        let guarded = type_checking_lines(parsed);
        let mut edges: BTreeMap<String, BTreeMap<usize, String>> = BTreeMap::new();
        for statement in &statements {
            let line = statement.line(parsed);
            let contents = parsed.text.text
                [parsed.text.line_start(line)..parsed.text.line_end(line)]
                .trim()
                .to_owned();
            for name in statement.imported_objects() {
                let Some(absolute) = absolute_name(module, source.is_package, &name) else {
                    continue;
                };
                if let Some(imported) = self.resolve(&absolute) {
                    edges
                        .entry(imported)
                        .or_default()
                        .insert(line, contents.clone());
                }
            }
        }
        let mut by_line: HashMap<usize, Vec<&ImportStatement<'_>>> = HashMap::new();
        for statement in &statements {
            by_line
                .entry(statement.line(parsed))
                .or_default()
                .push(statement);
        }
        let mut details = Vec::new();
        for (imported, lines) in edges {
            let external = !self.roots.iter().any(|root| is_within(&imported, root));
            for (line, contents) in lines {
                let nodes: Vec<&ImportStatement<'_>> = by_line
                    .get(&line)
                    .into_iter()
                    .flatten()
                    .copied()
                    .filter(|node| node.matches(module, source.is_package, &imported))
                    .collect();
                let describe = |node: Option<&ImportStatement<'_>>| {
                    let description = describe(
                        node,
                        parsed,
                        module,
                        source.is_package,
                        &imported,
                        &contents,
                    );
                    ImportDetail {
                        importer: module.to_owned(),
                        imported: imported.clone(),
                        line,
                        line_contents: contents.clone(),
                        names: description.names,
                        type_checking: guarded.contains(&line),
                        external,
                        column: description.column,
                        end_column: description.end_column,
                    }
                };
                if nodes.is_empty() {
                    details.push(describe(None));
                } else {
                    details.extend(nodes.into_iter().map(|node| describe(Some(node))));
                }
            }
        }
        details.sort_by(|left, right| {
            (left.line, left.column, &left.imported).cmp(&(
                right.line,
                right.column,
                &right.imported,
            ))
        });
        details
    }

    /// The module an imported object belongs to, or `None` when it leaves no edge.
    fn resolve(&self, name: &str) -> Option<String> {
        if self.internal.contains(name) {
            return Some(name.to_owned());
        }
        if let Some((parent, _)) = name.rsplit_once('.')
            && self.internal.contains(parent)
        {
            return Some(parent.to_owned());
        }
        let distilled = self.distill(name)?;
        // A missing module below an analyzed package is not part of the graph.
        let inside = self.found.iter().any(|root| is_within(&distilled, root));
        let exists = self.files.sources.contains_key(&distilled)
            || self.files.packages.binary_search(&distilled).is_ok();
        (!inside || exists).then_some(distilled)
    }

    /// The shallowest name of an external module that clashes with no internal
    /// namespace: `foo.blue.alpha` next to the package `foo.blue.beta` stays whole.
    fn distill(&self, name: &str) -> Option<String> {
        let root = name.split('.').next().unwrap_or_default();
        let descendant = |package: &str, ancestor: &str| {
            package
                .strip_prefix(ancestor)
                .is_some_and(|rest| rest.starts_with('.'))
        };
        if self.found.iter().any(|package| descendant(package, name)) {
            return None;
        }
        let mut deepest: Option<Vec<&str>> = None;
        for package in &self.found {
            if !descendant(package, root) {
                continue;
            }
            let internal: Vec<&str> = package.split('.').collect();
            let external: Vec<&str> = name.split('.').collect();
            let shared = internal
                .iter()
                .zip(&external)
                .take_while(|(left, right)| left == right)
                .count();
            if shared == internal.len() {
                return None;
            }
            let candidate = external[..=shared].to_vec();
            if deepest
                .as_ref()
                .is_none_or(|best| candidate.len() > best.len())
            {
                deepest = Some(candidate);
            }
        }
        Some(deepest.map_or_else(|| root.to_owned(), |parts| parts.join(".")))
    }
}

/// An imported name made absolute; `None` when it climbs above the top level.
fn absolute_name(module: &str, is_package: bool, name: &str) -> Option<String> {
    let dots = name.chars().take_while(|item| *item == '.').count();
    if dots == 0 {
        return Some(name.to_owned());
    }
    let parts: Vec<&str> = module.split('.').collect();
    let base = if is_package && dots == 1 {
        module.to_owned()
    } else {
        let kept = if is_package {
            parts.len() + 1
        } else {
            parts.len()
        };
        parts[..kept.checked_sub(dots)?].join(".")
    };
    Some(format!("{base}.{}", &name[dots..]))
}

#[derive(Clone, Copy)]
enum ImportStatement<'a> {
    Plain(&'a StmtImport),
    From(&'a StmtImportFrom),
}

impl<'a> ImportStatement<'a> {
    fn start(self) -> TextSize {
        match self {
            Self::Plain(node) => node.start(),
            Self::From(node) => node.start(),
        }
    }

    fn line(self, parsed: &ParsedSource) -> usize {
        parsed.text.line_at(usize::from(self.start()))
    }

    fn aliases(self) -> &'a [Alias] {
        match self {
            Self::Plain(node) => &node.names,
            Self::From(node) => &node.names,
        }
    }

    /// The names as written, with the leading dots of a relative import.
    fn imported_objects(self) -> Vec<String> {
        match self {
            Self::Plain(node) => node.names.iter().map(|a| a.name.to_string()).collect(),
            Self::From(node) => {
                let dots = ".".repeat(usize::try_from(node.level).unwrap_or(0));
                node.names
                    .iter()
                    .map(|alias| match &node.module {
                        Some(module) => format!("{dots}{module}.{}", alias.name),
                        None => format!("{dots}{}", alias.name),
                    })
                    .collect()
            }
        }
    }

    fn matches(self, module: &str, is_package: bool, imported: &str) -> bool {
        let names: Vec<String> = match self {
            Self::Plain(node) => node.names.iter().map(|a| a.name.to_string()).collect(),
            Self::From(node) => {
                let base = from_base(node, module, is_package);
                node.names
                    .iter()
                    .map(|alias| {
                        if alias.name.as_str() == "*" {
                            base.clone()
                        } else {
                            format!("{base}.{}", alias.name)
                        }
                    })
                    .collect()
            }
        };
        names
            .iter()
            .any(|name| is_within(name, imported) || is_within(imported, name))
    }
}

fn from_base(node: &StmtImportFrom, module: &str, is_package: bool) -> String {
    resolve_relative(
        module,
        is_package,
        node.level,
        node.module.as_ref().map(|name| name.as_str()),
    )
}

fn import_statements(parsed: &ParsedSource) -> Vec<ImportStatement<'_>> {
    tree_nodes(parsed.module())
        .into_iter()
        .filter_map(|node| match node {
            TreeNode::Statement(Stmt::Import(item)) => Some(ImportStatement::Plain(item)),
            TreeNode::Statement(Stmt::ImportFrom(item)) => Some(ImportStatement::From(item)),
            _ => None,
        })
        .collect()
}

pub fn is_type_checking(test: &Expr) -> bool {
    match test {
        Expr::Name(name) => name.id.as_str() == "TYPE_CHECKING",
        Expr::Attribute(attribute) => attribute.attr.as_str() == "TYPE_CHECKING",
        _ => false,
    }
}

/// Line numbers inside `if TYPE_CHECKING:` blocks, which also covers an `elif`.
pub fn type_checking_lines(parsed: &ParsedSource) -> HashSet<usize> {
    let mut lines = HashSet::new();
    let mut add_body = |body: &[Stmt]| {
        for child in body {
            let first = parsed
                .text
                .line_at(usize::from(parsed.statement_start(child)));
            let last = parsed.text.line_at(usize::from(child.end()));
            lines.extend(first..=last);
        }
    };
    for node in tree_nodes(parsed.module()) {
        let TreeNode::Statement(Stmt::If(item)) = node else {
            continue;
        };
        if is_type_checking(&item.test) {
            add_body(&item.body);
        }
        for clause in &item.elif_else_clauses {
            if clause.test.as_ref().is_some_and(is_type_checking) {
                add_body(&clause.body);
            }
        }
    }
    lines
}

struct Description {
    names: Vec<String>,
    column: usize,
    end_column: usize,
}

fn describe(
    node: Option<&ImportStatement<'_>>,
    parsed: &ParsedSource,
    module: &str,
    is_package: bool,
    imported: &str,
    contents: &str,
) -> Description {
    match node {
        None => Description {
            names: vec![imported.to_owned()],
            column: 1,
            end_column: contents.chars().count() + 1,
        },
        Some(ImportStatement::Plain(item)) => describe_import(item, parsed, imported, contents),
        Some(ImportStatement::From(item)) => {
            describe_import_from(item, parsed, module, is_package, imported)
        }
    }
}

fn place(parsed: &ParsedSource, offset: TextSize) -> (usize, usize) {
    parsed.text.position(usize::from(offset))
}

fn describe_import(
    node: &StmtImport,
    parsed: &ParsedSource,
    imported: &str,
    contents: &str,
) -> Description {
    let (line, start_column) = place(parsed, node.start());
    let (end_line, end_column) = place(parsed, node.end());
    let first_line_end = if end_line == line {
        end_column
    } else {
        contents.chars().count()
    };
    let matching: Vec<&Alias> = node
        .names
        .iter()
        .filter(|alias| {
            is_within(alias.name.as_str(), imported) || is_within(imported, alias.name.as_str())
        })
        .collect();
    let names = if matching.is_empty() {
        vec![imported.to_owned()]
    } else {
        matching.iter().map(|a| a.name.to_string()).collect()
    };
    if let [alias] = matching.as_slice()
        && place(parsed, alias.start()).0 == line
    {
        return Description {
            names,
            column: place(parsed, alias.start()).1 + 1,
            end_column: place(parsed, alias.end()).1 + 1,
        };
    }
    Description {
        names,
        column: start_column + 1,
        end_column: first_line_end + 1,
    }
}

fn describe_import_from(
    node: &StmtImportFrom,
    parsed: &ParsedSource,
    module: &str,
    is_package: bool,
    imported: &str,
) -> Description {
    let base = from_base(node, module, is_package);
    let (line, start_column) = place(parsed, node.start());
    let names: Vec<String> = node
        .names
        .iter()
        .map(|alias| {
            if base.is_empty() {
                alias.name.to_string()
            } else {
                format!("{base}.{}", alias.name)
            }
        })
        .collect();
    let span = |alias: &Alias| {
        (
            place(parsed, alias.start()).1 + 1,
            place(parsed, alias.end()).1 + 1,
        )
    };
    for (alias, name) in node.names.iter().zip(&names) {
        if name == imported && place(parsed, alias.start()).0 == line {
            let (column, end_column) = span(alias);
            return Description {
                names: vec![name.clone()],
                column,
                end_column,
            };
        }
    }
    let matching: Vec<String> = names
        .iter()
        .filter(|name| is_within(name, imported) || is_within(imported, name))
        .cloned()
        .collect();
    let selected = if matching.is_empty() { names } else { matching };
    if let [alias] = &*node.names
        && place(parsed, alias.start()).0 == line
    {
        let (column, end_column) = span(alias);
        return Description {
            names: selected,
            column,
            end_column,
        };
    }
    let start = start_column + "from ".len();
    let level = usize::try_from(node.level).unwrap_or(0);
    let written = level + node.module.as_ref().map_or(0, |name| name.as_str().len());
    Description {
        names: selected,
        column: start + 1,
        end_column: start + written + 1,
    }
}

/// `str.splitlines` without the line ends.
fn python_lines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut characters = text.char_indices().peekable();
    while let Some((index, character)) = characters.next() {
        if !matches!(
            character,
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        ) {
            continue;
        }
        lines.push(&text[start..index]);
        if character == '\r' && characters.peek().is_some_and(|(_, next)| *next == '\n') {
            characters.next();
            start = index + 2;
        } else {
            start = index + character.len_utf8();
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

fn test_details(
    test: &SourceFile,
    parsed: &ParsedSource,
    known: &HashSet<&str>,
) -> Vec<ImportDetail> {
    let lines = python_lines(&parsed.text.text);
    let guarded = type_checking_lines(parsed);
    let is_package = test.module.ends_with(".__init__");
    let mut details = Vec::new();
    for statement in import_statements(parsed) {
        let line = statement.line(parsed);
        let contents = lines.get(line - 1).copied().unwrap_or_default();
        for imported in project_targets(statement, &test.module, is_package, known) {
            let description = describe(
                Some(&statement),
                parsed,
                &test.module,
                is_package,
                &imported,
                contents,
            );
            details.push(ImportDetail {
                importer: test.module.clone(),
                imported,
                line,
                line_contents: contents.to_owned(),
                names: description.names,
                type_checking: guarded.contains(&line),
                external: false,
                column: description.column,
                end_column: description.end_column,
            });
        }
    }
    details
}

fn project_targets(
    statement: ImportStatement<'_>,
    module: &str,
    is_package: bool,
    known: &HashSet<&str>,
) -> BTreeSet<String> {
    match statement {
        ImportStatement::Plain(_) => statement
            .aliases()
            .iter()
            .map(|alias| alias.name.as_str())
            .filter(|name| known.contains(name))
            .map(str::to_owned)
            .collect(),
        ImportStatement::From(node) => {
            let base = from_base(node, module, is_package);
            if !known.contains(base.as_str()) {
                return BTreeSet::new();
            }
            node.names
                .iter()
                .map(|alias| {
                    let name = format!("{base}.{}", alias.name);
                    if known.contains(name.as_str()) {
                        name
                    } else {
                        base.clone()
                    }
                })
                .collect()
        }
    }
}
