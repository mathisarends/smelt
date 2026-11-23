//! ARC004: import cycles between architecture nodes or concrete modules.
use super::{GraphContext, ImportFinding, import_violation, skip_import, wiring_facade};
use crate::checker::analysis::architecture::{ModuleInfo, ModuleKind};
use crate::checker::analysis::imports::ImportDetail;
use crate::checker::patterns::is_within;
use crate::checker::settings::CatalogRule;
use crate::checker::violation::{ImportLink, Violation};
use serde_json::{Map, Value, json};
use std::collections::btree_set;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};

/// A witness hop inside one node is short; a longer path explains little.
const MAX_INNER_HOPS: usize = 6;
const CONCRETE_MODULES: &str = "concrete modules";

/// A graph node, ordered by its Python `str()` as the original sorts it.
#[derive(Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
struct Node {
    sort: String,
    label: String,
}

impl Node {
    fn plain(name: &str) -> Self {
        Self {
            sort: name.to_owned(),
            label: name.to_owned(),
        }
    }

    fn sibling(module: &str) -> Self {
        Self {
            sort: module.to_owned(),
            label: module
                .rsplit_once('.')
                .map_or(module, |(_, last)| last)
                .to_owned(),
        }
    }

    fn layer(feature: Option<&str>, layer: &str) -> Self {
        let sort = match feature {
            Some(feature) => format!("('{feature}', '{layer}')"),
            None => format!("(None, '{layer}')"),
        };
        let label = match feature {
            Some(feature) => format!("{feature}.{layer}"),
            None => layer.to_owned(),
        };
        Self { sort, label }
    }
}

type Adjacency = BTreeMap<Node, BTreeSet<Node>>;
type NodeOf<'f> = &'f dyn Fn(&str) -> Option<Node>;
type ModuleEdges<'a> = HashMap<&'a str, BTreeSet<&'a str>>;

#[derive(Default)]
struct Graph<'a> {
    adjacency: Adjacency,
    witnesses: HashMap<(Node, Node), Vec<&'a ImportDetail>>,
}

impl<'a> Graph<'a> {
    fn add(&mut self, from: Node, to: Node, detail: &'a ImportDetail) {
        if from == to {
            return;
        }
        self.adjacency
            .entry(from.clone())
            .or_default()
            .insert(to.clone());
        self.witnesses.entry((from, to)).or_default().push(detail);
    }

    fn edge(&self, from: &Node, to: &Node) -> &[&'a ImportDetail] {
        self.witnesses
            .get(&(from.clone(), to.clone()))
            .map_or(&[], Vec::as_slice)
    }

    /// One import per hop, preferring imports that connect module to module.
    fn chain(&self, cycle: &[Node]) -> Vec<&'a ImportDetail> {
        let mut hops: Vec<&'a ImportDetail> = Vec::new();
        for pair in cycle.windows(2) {
            let candidates = self.edge(&pair[0], &pair[1]);
            let previous = hops.last().map(|hop| hop.imported.as_str());
            let connected = candidates
                .iter()
                .find(|detail| Some(detail.importer.as_str()) == previous);
            if let Some(hop) = connected.or_else(|| candidates.first()) {
                hops.push(hop);
            }
        }
        hops
    }

    /// Every import from `from` to `to` sits under `if TYPE_CHECKING:`.
    fn type_checking_only(&self, from: &Node, to: &Node) -> bool {
        self.edge(from, to)
            .iter()
            .all(|detail| detail.type_checking)
    }

    fn runtime(&self) -> Graph<'a> {
        let mut graph = Graph::default();
        for ((from, to), imports) in &self.witnesses {
            for detail in imports {
                if !detail.type_checking {
                    graph.add(from.clone(), to.clone(), detail);
                }
            }
        }
        graph
    }
}

pub fn check(rule: &CatalogRule, context: &GraphContext<'_>) -> Vec<Violation> {
    let model = context.model;
    let policy = &model.config.import_graph;
    let scopes: HashSet<&str> = policy.cycles.iter().map(String::as_str).collect();
    let covered = packages_covered_by_other_scopes(context, &scopes);
    let mut features = Graph::default();
    let mut layers = Graph::default();
    let mut siblings: BTreeMap<String, Graph<'_>> = BTreeMap::new();
    let mut modules = Graph::default();
    for detail in context.imports.all_imports() {
        if detail.external
            || skip_import(context, detail)
            || (detail.type_checking && policy.ignore_type_checking_cycles)
        {
            continue;
        }
        let (Some(source), Some(target)) =
            (model.info(&detail.importer), model.info(&detail.imported))
        else {
            continue;
        };
        if reported_elsewhere(context, &source, &target) {
            continue;
        }
        if source.in_grid() && target.in_grid() {
            if let (Some(from), Some(to)) = (&source.feature, &target.feature) {
                features.add(Node::plain(from), Node::plain(to), detail);
            }
            if source.feature == target.feature
                && let (Some(from), Some(to)) = (&source.layer, &target.layer)
            {
                layers.add(
                    Node::layer(source.feature.as_deref(), from),
                    Node::layer(target.feature.as_deref(), to),
                    detail,
                );
            }
        }
        modules.add(
            Node::plain(&detail.importer),
            Node::plain(&detail.imported),
            detail,
        );
        if let Some((parent, from, to)) = sibling_pair(&detail.importer, &detail.imported)
            && !covered.contains(&parent)
        {
            siblings.entry(parent).or_default().add(
                Node::sibling(&from),
                Node::sibling(&to),
                detail,
            );
        }
    }
    let include = policy.include_type_checking && !policy.ignore_type_checking_cycles;
    let edges = first_party_edges(context, include);
    let report = Report {
        rule,
        context,
        edges: &edges,
        include,
    };
    let mut found = Vec::new();
    if scopes.contains("features") {
        let node_of = |module: &str| {
            let info = model.info(module)?;
            info.feature.as_deref().map(Node::plain)
        };
        found.extend(report.scope(&features, "features", &node_of));
    }
    if scopes.contains("layers") {
        let node_of = |module: &str| {
            let info = model.info(module)?;
            let layer = info.layer.as_deref()?;
            Some(Node::layer(info.feature.as_deref(), layer))
        };
        found.extend(report.scope(&layers, "layers", &node_of));
    }
    if scopes.contains("siblings") {
        for (parent, graph) in &siblings {
            let node_of =
                |module: &str| child_of(parent, module).map(|child| Node::sibling(&child));
            found.extend(report.scope(graph, &format!("modules in {parent}"), &node_of));
        }
    }
    if scopes.contains("modules") {
        let node_of = |module: &str| Some(Node::plain(module));
        found.extend(report.scope(&modules, CONCRETE_MODULES, &node_of));
    }
    found
}

struct Report<'r, 'c> {
    rule: &'r CatalogRule,
    context: &'r GraphContext<'c>,
    edges: &'r ModuleEdges<'c>,
    include: bool,
}

impl Report<'_, '_> {
    fn scope(&self, graph: &Graph<'_>, scope: &str, node_of: NodeOf<'_>) -> Vec<Violation> {
        let concrete = scope == CONCRETE_MODULES;
        let runtime = concrete.then(|| graph.runtime());
        let mut found = Vec::new();
        for component in strongly_connected_components(&graph.adjacency) {
            if component.len() < 2 {
                continue;
            }
            let Some(start) = component.first() else {
                continue;
            };
            let mut cycle = shortest_cycle(start, &graph.adjacency, &component);
            let mut witness_graph = graph;
            if let Some(runtime) = &runtime
                && let Some(runtime_cycle) = runtime_cycle(runtime, &component)
            {
                cycle = Some(runtime_cycle);
                witness_graph = runtime;
            }
            let Some(cycle) = cycle else {
                continue;
            };
            let hops = witness_graph.chain(&cycle);
            let chain = self.witness(&hops, node_of);
            found.push(self.violation(
                graph,
                witness_graph,
                scope,
                (&component, &cycle),
                (&hops, chain),
            ));
        }
        found
    }

    fn violation(
        &self,
        graph: &Graph<'_>,
        witness_graph: &Graph<'_>,
        scope: &str,
        (component, cycle): (&BTreeSet<Node>, &[Node]),
        (hops, chain): (&[&ImportDetail], Vec<ImportLink>),
    ) -> Violation {
        let concrete = scope == CONCRETE_MODULES;
        let names: Vec<&str> = cycle.iter().map(|node| node.label.as_str()).collect();
        let arrow = names.join(" -> ");
        let statics: Vec<String> = cycle
            .windows(2)
            .filter(|pair| witness_graph.type_checking_only(&pair[0], &pair[1]))
            .map(|pair| format!("{} -> {}", pair[0].label, pair[1].label))
            .collect();
        let mut message = format!("import cycle between {scope}: {arrow}");
        let mut members: Vec<&str> = component.iter().map(|node| node.label.as_str()).collect();
        members.sort_unstable();
        let mut cyclic_edges: Vec<String> = component
            .iter()
            .flat_map(|from| {
                graph
                    .adjacency
                    .get(from)
                    .into_iter()
                    .flatten()
                    .filter(|to| component.contains(*to))
                    .map(move |to| format!("{scope}:{} -> {}", from.label, to.label))
            })
            .collect();
        cyclic_edges.sort_unstable();
        let mut expected = Map::new();
        expected.insert("cycle".to_owned(), json!(names));
        expected.insert("members".to_owned(), json!(members));
        expected.insert("cyclic_edges".to_owned(), json!(cyclic_edges));
        expected.insert(
            "graph_kind".to_owned(),
            json!(if concrete {
                "concrete-modules"
            } else {
                "aggregated"
            }),
        );
        expected.insert("witness_edges".to_owned(), self.edge_phases(&chain));
        let mut hint = "Break one edge: extract what both sides need into a separate module, or \
                        invert one dependency behind an abstraction."
            .to_owned();
        if !concrete {
            hint.push_str(
                " This graph aggregates architecture nodes; its witness does not prove a \
                 concrete module runtime cycle. Select architecture.import_graph.cycles: \
                 [modules] to review concrete module edges.",
            );
        }
        let distinct: HashSet<&Node> = cycle.iter().collect();
        if component.len() > distinct.len() {
            hint.push_str(&format!(
                " The cyclic group contains: {}.",
                members.join(", ")
            ));
        }
        if !statics.is_empty() {
            message.push_str(" (type checking only)");
            expected.insert("type_checking_only".to_owned(), json!(statics));
            let verb = if statics.len() == 1 { "is" } else { "are" };
            hint = format!(
                "Only type checkers see this cycle: {} {verb} imported under `if TYPE_CHECKING:`, \
                 so it cannot fail at import time. It still couples both sides. {hint} To leave \
                 such edges out, set architecture.import_graph.type_checking: ignore.",
                statics.join(", ")
            );
        }
        import_violation(
            self.rule,
            self.context,
            hops[0],
            ImportFinding {
                message,
                expected: Value::Object(expected),
                hint: Some(hint),
                chain,
                edge: Some(arrow),
            },
        )
    }

    /// The cycle's imports, joined inside each node where a module path exists.
    fn witness(&self, hops: &[&ImportDetail], node_of: NodeOf<'_>) -> Vec<ImportLink> {
        let mut links = Vec::new();
        for (index, hop) in hops.iter().enumerate() {
            links.push(ImportLink {
                importer: hop.importer.clone(),
                imported: hop.imported.clone(),
                line: Some(hop.line),
            });
            let Some(following) = hops.get(index + 1) else {
                continue;
            };
            if following.importer == hop.imported {
                continue;
            }
            let path = path_within(self.edges, &hop.imported, &following.importer, node_of);
            for pair in path.windows(2) {
                links.push(ImportLink {
                    importer: pair[0].to_owned(),
                    imported: pair[1].to_owned(),
                    line: self.line(pair[0], pair[1]),
                });
            }
        }
        links
    }

    fn line(&self, importer: &str, imported: &str) -> Option<usize> {
        self.context
            .imports
            .imports_of(importer)
            .iter()
            .find(|detail| detail.imported == imported && (self.include || !detail.type_checking))
            .map(|detail| detail.line)
    }

    fn edge_phases(&self, chain: &[ImportLink]) -> Value {
        let phases: Vec<Value> = chain
            .iter()
            .map(|link| {
                let phase = self
                    .context
                    .imports
                    .imports_of(&link.importer)
                    .iter()
                    .find(|detail| {
                        detail.imported == link.imported && Some(detail.line) == link.line
                    })
                    .map_or("unknown", |detail| {
                        if detail.type_checking {
                            "type-checking"
                        } else {
                            "runtime"
                        }
                    });
                json!({
                    "importer": link.importer,
                    "imported": link.imported,
                    "line": link.line,
                    "phase": phase,
                })
            })
            .collect();
        Value::Array(phases)
    }
}

fn first_party_edges<'a>(context: &GraphContext<'a>, include: bool) -> ModuleEdges<'a> {
    let mut edges: ModuleEdges<'a> = HashMap::new();
    for detail in context.imports.all_imports() {
        if detail.external || (detail.type_checking && !include) {
            continue;
        }
        edges
            .entry(detail.importer.as_str())
            .or_default()
            .insert(detail.imported.as_str());
    }
    edges
}

fn path_within<'e>(
    edges: &ModuleEdges<'e>,
    start: &'e str,
    goal: &str,
    node_of: NodeOf<'_>,
) -> Vec<&'e str> {
    let node = node_of(start);
    let mut previous: HashMap<&'e str, Option<&'e str>> = HashMap::from([(start, None)]);
    let mut frontier = vec![start];
    for _ in 0..MAX_INNER_HOPS {
        let mut following = Vec::new();
        for module in frontier {
            for &child in edges.get(module).into_iter().flatten() {
                if previous.contains_key(child) || node_of(child) != node {
                    continue;
                }
                previous.insert(child, Some(module));
                if child == goal {
                    let mut path = vec![child];
                    while let Some(parent) = path.last().and_then(|last| previous[last]) {
                        path.push(parent);
                    }
                    path.reverse();
                    return path;
                }
                following.push(child);
            }
        }
        frontier = following;
    }
    Vec::new()
}

fn children<'g>(
    adjacency: &'g Adjacency,
    empty: &'g BTreeSet<Node>,
    node: &Node,
) -> btree_set::Iter<'g, Node> {
    adjacency.get(node).unwrap_or(empty).iter()
}

/// Tarjan's algorithm, iterative so deep graphs cannot exhaust the stack.
fn strongly_connected_components(adjacency: &Adjacency) -> Vec<BTreeSet<Node>> {
    let empty = BTreeSet::new();
    let mut nodes: BTreeSet<&Node> = adjacency.keys().collect();
    nodes.extend(adjacency.values().flatten());
    let mut index_of: HashMap<&Node, usize> = HashMap::new();
    let mut lowlink: HashMap<&Node, usize> = HashMap::new();
    let mut on_stack: HashSet<&Node> = HashSet::new();
    let mut stack: Vec<&Node> = Vec::new();
    let mut components = Vec::new();
    let mut counter = 0;
    for start in nodes {
        if index_of.contains_key(start) {
            continue;
        }
        index_of.insert(start, counter);
        lowlink.insert(start, counter);
        counter += 1;
        stack.push(start);
        on_stack.insert(start);
        let mut work = vec![(start, children(adjacency, &empty, start))];
        while let Some((node, pending)) = work.last_mut() {
            let node = *node;
            if let Some(child) = pending.next() {
                if let Some(&seen) = index_of.get(child) {
                    if on_stack.contains(child) {
                        let low = lowlink[node].min(seen);
                        lowlink.insert(node, low);
                    }
                } else {
                    index_of.insert(child, counter);
                    lowlink.insert(child, counter);
                    counter += 1;
                    stack.push(child);
                    on_stack.insert(child);
                    work.push((child, children(adjacency, &empty, child)));
                }
                continue;
            }
            work.pop();
            if let Some((parent, _)) = work.last() {
                let low = lowlink[*parent].min(lowlink[node]);
                lowlink.insert(*parent, low);
            }
            if lowlink[node] == index_of[node] {
                let mut component = BTreeSet::new();
                while let Some(member) = stack.pop() {
                    on_stack.remove(member);
                    component.insert(member.clone());
                    if member == node {
                        break;
                    }
                }
                components.push(component);
            }
        }
    }
    components
}

/// The shortest path `start -> ... -> start` using only nodes in `within`.
fn shortest_cycle(
    start: &Node,
    adjacency: &Adjacency,
    within: &BTreeSet<Node>,
) -> Option<Vec<Node>> {
    let mut previous: HashMap<&Node, &Node> = HashMap::new();
    let mut queue: VecDeque<&Node> = VecDeque::from([start]);
    let mut visited: HashSet<&Node> = HashSet::new();
    while let Some(node) = queue.pop_front() {
        for child in adjacency.get(node).into_iter().flatten() {
            if !within.contains(child) {
                continue;
            }
            if child == start {
                let mut path = vec![node];
                while let Some(last) = path.last().copied()
                    && last != start
                {
                    path.push(previous[last]);
                }
                let mut cycle: Vec<Node> = path.into_iter().rev().cloned().collect();
                cycle.push(start.clone());
                return Some(cycle);
            }
            if visited.insert(child) {
                previous.insert(child, node);
                queue.push_back(child);
            }
        }
    }
    None
}

fn runtime_cycle(graph: &Graph<'_>, within: &BTreeSet<Node>) -> Option<Vec<Node>> {
    let mut components = strongly_connected_components(&graph.adjacency);
    components.sort_by(|left, right| left.first().cmp(&right.first()));
    let component = components
        .iter()
        .find(|component| component.len() > 1 && component.is_subset(within))?;
    shortest_cycle(component.first()?, &graph.adjacency, component)
}

/// The child of `parent` containing `module`: the node of a sibling cycle.
fn child_of(parent: &str, module: &str) -> Option<String> {
    if !is_within(module, parent) || module == parent {
        return None;
    }
    let depth = parent.matches('.').count() + 2;
    Some(module.split('.').take(depth).collect::<Vec<_>>().join("."))
}

/// Ignore wiring edges and imports already reported by boundary rules.
fn reported_elsewhere(
    context: &GraphContext<'_>,
    source: &ModuleInfo,
    target: &ModuleInfo,
) -> bool {
    if source.wiring {
        return true;
    }
    if (target.kind == ModuleKind::CompositionRoot || target.wiring)
        && source.kind != ModuleKind::CompositionRoot
        && !wiring_facade(context.model, source, target)
    {
        return true;
    }
    if !(source.in_grid() && target.in_grid()) {
        return false;
    }
    let (Some(source_layer), Some(target_layer)) = (&source.layer, &target.layer) else {
        return false;
    };
    let config = context.model.config;
    if source.feature == target.feature {
        let allowed = config
            .layer(source_layer)
            .is_some_and(|layer| layer.may_depend_on.contains(target_layer));
        return source_layer != target_layer && !allowed;
    }
    let cross = &config.cross_feature;
    cross.deny_by_default
        && match (&source.feature, &target.feature) {
            (Some(source_feature), Some(target_feature)) => !cross.allows(
                (source_feature, source_layer),
                (target_feature, target_layer),
            ),
            _ => false,
        }
}

fn packages_covered_by_other_scopes(
    context: &GraphContext<'_>,
    scopes: &HashSet<&str>,
) -> HashSet<String> {
    let model = context.model;
    let mut covered = HashSet::new();
    if scopes.contains("features") && model.has_features() {
        covered.extend(model.feature_packages().map(|package| {
            package
                .rsplit_once('.')
                .map_or(package, |(parent, _)| parent)
                .to_owned()
        }));
    }
    if scopes.contains("layers") && !model.config.layers.is_empty() {
        covered.extend(model.grid_containers());
    }
    covered
}

/// The common parent and the two children an import crosses between.
fn sibling_pair(importer: &str, imported: &str) -> Option<(String, String, String)> {
    if is_within(importer, imported) || is_within(imported, importer) {
        return None;
    }
    let left: Vec<&str> = importer.split('.').collect();
    let right: Vec<&str> = imported.split('.').collect();
    let common = left
        .iter()
        .zip(&right)
        .take_while(|(first, second)| first == second)
        .count();
    if common == 0 {
        return None;
    }
    Some((
        left[..common].join("."),
        left[..=common].join("."),
        right[..=common].join("."),
    ))
}
