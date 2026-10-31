"""Stable definition ordering within execution barriers, without running source."""

from __future__ import annotations

import ast
import heapq
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from smelt.analysis.definition_blocks import (
    DefinitionBlock,
    ScopeNode,
    build_blocks,
    is_main_guard,
)
from smelt.config.models import OrderingConfig

if TYPE_CHECKING:
    from smelt.analysis.contracts import ContractIndex
    from smelt.analysis.files import FileIndex, SourceFile
    from smelt.analysis.parsing import AstCache
    from smelt.analysis.python_version import PythonTargetIndex


@dataclass(frozen=True)
class OrderSection:
    owner: ScopeNode
    scope: str
    blocks: tuple[DefinitionBlock, ...]
    order: tuple[int, ...]
    edges: tuple[str, ...]
    fix_reason: str | None = None
    fix_blocker: _FixBlocker | None = None

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.blocks[0].line}:{self.blocks[-1].end_line}"

    @property
    def line(self) -> int:
        return self.blocks[0].line

    @property
    def original(self) -> list[str]:
        return [block.label for block in self.blocks]

    @property
    def expected(self) -> list[str]:
        return [self.blocks[index].label for index in self.order]


@dataclass(frozen=True)
class OrderIssue:
    scope: str
    line: int
    message: str
    edges: tuple[str, ...]
    section: OrderSection | None = None
    reason: str | None = None
    fix_node: ast.stmt | ast.expr | ast.type_param | None = None


@dataclass
class FileOrder:
    path: str
    module: str
    tree: ast.Module
    config: OrderingConfig
    contract: str
    deferred: bool
    sections: list[OrderSection] = field(default_factory=list)
    issues: list[OrderIssue] = field(default_factory=list)
    barriers: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "contract": self.contract,
            "deferred_annotations": self.deferred,
            "sections": len(self.sections),
            "barriers": self.barriers,
        }


class DefinitionOrderIndex:
    def __init__(
        self,
        files: FileIndex,
        contracts: ContractIndex,
        asts: AstCache,
        python_targets: PythonTargetIndex,
    ) -> None:
        self.files: dict[str, FileOrder] = {}
        for source in files.sources.values():
            contract = contracts.policy_for(source.module, "ordering")
            tree = asts.parse(source.path)
            future = any(
                isinstance(node, ast.ImportFrom)
                and node.module == "__future__"
                and node.level == 0
                and any(alias.name == "annotations" for alias in node.names)
                for node in tree.body
            )
            deferred = (
                future or python_targets.for_path(source.absolute).deferred_annotations
            )
            self.files[source.path] = _analyze_order(
                tree,
                contract.spec.ordering
                if contract and contract.spec.ordering
                else OrderingConfig(),
                source=source,
                contract=contract.name
                if contract
                else "rules.public-definitions-first",
                deferred=deferred,
            )

    def summary(self) -> list[dict[str, Any]]:
        return [result.summary() for _, result in sorted(self.files.items())]


@dataclass(frozen=True)
class _FixBlocker:
    reason: str
    node: ast.stmt | ast.expr | ast.type_param


def _precedence(blocks: tuple[DefinitionBlock, ...]) -> list[set[int]]:
    """Preserve group order, binding versions, and reads before later rebinding."""
    before: list[set[int]] = [set() for _ in blocks]
    last_group: dict[str, int] = {}
    writers: dict[str, list[int]] = defaultdict(list)
    for index, block in enumerate(blocks):
        if block.category in last_group:
            before[index].add(last_group[block.category])
        last_group[block.category] = index
        for name in block.binds:
            if writers[name]:
                before[index].add(writers[name][-1])
            writers[name].append(index)
    for index, block in enumerate(blocks):
        for name in block.reads:
            prior = [writer for writer in writers[name] if writer < index]
            later = [writer for writer in writers[name] if writer > index]
            if prior:
                before[index].add(prior[-1])
            if later:
                before[later[0]].add(index)
    return before


def _canonical_order(
    blocks: tuple[DefinitionBlock, ...], priorities: list[str]
) -> tuple[int, ...]:
    dependencies = _precedence(blocks)
    ranks = {name: index for index, name in enumerate(priorities)}
    followers: dict[int, list[int]] = defaultdict(list)
    for index, required in enumerate(dependencies):
        for prerequisite in required:
            followers[prerequisite].append(index)
    priority = [ranks.get(block.category, len(ranks)) for block in blocks]
    for index in reversed(range(len(blocks))):
        for follower in followers[index]:
            priority[index] = min(priority[index], priority[follower])
    ready = [
        (priority[index], index)
        for index in range(len(blocks))
        if not dependencies[index]
    ]
    heapq.heapify(ready)
    result: list[int] = []
    while ready:
        _, index = heapq.heappop(ready)
        result.append(index)
        for follower in followers[index]:
            dependencies[follower].remove(index)
            if not dependencies[follower]:
                heapq.heappush(ready, (priority[follower], follower))
    # All constraints preserve an existing ordering relation, so they form a DAG.
    return tuple(result)


def _section(
    result: FileOrder,
    owner: ScopeNode,
    scope: str,
    blocks: list[DefinitionBlock],
    fix_blocker: _FixBlocker | None,
) -> None:
    if not blocks:
        return
    group = tuple(blocks)
    priorities = (
        result.config.class_order
        if isinstance(owner, ast.ClassDef)
        else result.config.module
    )
    order = _canonical_order(group, list(priorities))
    positions = {index: position for position, index in enumerate(order)}
    edges = tuple(
        f"{group[left].key} -> {group[right].key}"
        for left in range(len(group))
        for right in range(left + 1, len(group))
        if positions[left] > positions[right]
    )
    factory = next((block for block in group if block.fix_reason), None)
    blocker = fix_blocker or (
        _FixBlocker(factory.fix_reason, factory.fix_node or factory.nodes[0])
        if factory and factory.fix_reason
        else None
    )
    reason = blocker.reason if blocker else None
    if result.config.fix == "off":
        reason = "ordering fixes are disabled for this package"
        blocker = None
    section = OrderSection(owner, scope, group, order, edges, reason, blocker)
    result.sections.append(section)
    if edges:
        first_changed = next(
            index for index, expected in enumerate(order) if index != expected
        )
        result.issues.append(
            OrderIssue(
                scope,
                group[first_changed].line,
                f"definitions in {scope} have {len(edges)} avoidable ordering inversion(s)",
                edges,
                section,
            )
        )


def _property_boundaries(blocks: list[DefinitionBlock]) -> list[DefinitionBlock]:
    families: dict[str, list[int]] = defaultdict(list)
    for index, block in enumerate(blocks):
        if any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and any(
                isinstance(decorator, ast.Attribute)
                and decorator.attr in {"getter", "setter", "deleter"}
                for decorator in node.decorator_list
            )
            for node in block.nodes
        ):
            families[block.label].append(index)
    for name in families:
        indices = [index for index, block in enumerate(blocks) if block.label == name]
        if len(indices) > 1:
            for index in indices:
                blocks[index] = replace(
                    blocks[index],
                    barrier="noncontiguous property family is kept in place",
                )
    return blocks


def _fixed_positions(result: FileOrder, blocks: list[DefinitionBlock]) -> None:
    seen: list[DefinitionBlock] = []
    ranks: dict[str, int] = {
        name: index for index, name in enumerate(result.config.module)
    }
    for index, block in enumerate(blocks):
        if block.category in {"imports", "future-imports"}:
            rank = ranks[block.category]
            late = [
                prior
                for prior in seen
                if prior.category in result.config.module
                and ranks[prior.category] > rank
            ]
            if late:
                edges = tuple(f"{prior.key} -> fixed:{block.key}" for prior in late)
                result.issues.append(
                    OrderIssue(
                        "module",
                        block.line,
                        f"{block.category} appear after later definition groups",
                        edges,
                        reason="imports are execution barriers; review placement manually",
                        fix_node=block.nodes[0],
                    )
                )
        if is_main_guard(block.nodes[0]) and index + 1 < len(blocks):
            edges = tuple(
                f"main:{block.key} -> {later.key}" for later in blocks[index + 1 :]
            )
            result.issues.append(
                OrderIssue(
                    "module",
                    block.line,
                    "the main guard must be the final module statement",
                    edges,
                    reason="moving the main guard changes execution timing; review manually",
                    fix_node=block.nodes[0],
                )
            )
        seen.append(block)


def _reflective(tree: ast.Module) -> _FixBlocker | None:
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Global | ast.Nonlocal)
        or (
            isinstance(node, ast.Name)
            and node.id
            in {"globals", "locals", "vars", "eval", "exec", "__annotations__"}
        )
        or (
            isinstance(node, ast.Attribute)
            and node.attr
            in {"__dict__", "__annotations__", "__code__", "settrace", "setprofile"}
        )
    ]
    return (
        _FixBlocker(
            "runtime namespace or annotation introspection requires manual review",
            min(found, key=lambda node: (node.lineno, node.col_offset)),
        )
        if found
        else None
    )


def _body(
    result: FileOrder,
    owner: ScopeNode,
    scope: str,
    aliases: dict[str, str],
    fix_blocker: _FixBlocker | None,
) -> None:
    blocks, children = build_blocks(
        owner,
        scope=scope,
        config=result.config,
        aliases=dict(aliases),
        deferred=result.deferred,
    )
    blocks = _property_boundaries(blocks)
    if isinstance(owner, ast.Module):
        _fixed_positions(result, blocks)
    group: list[DefinitionBlock] = []
    for block in blocks:
        if block.barrier:
            _section(result, owner, scope, group, fix_blocker)
            group = []
            result.barriers.append(
                {"scope": scope, "line": block.line, "reason": block.barrier}
            )
        else:
            group.append(block)
    _section(result, owner, scope, group, fix_blocker)
    counts: dict[str, int] = defaultdict(int)
    for child, child_aliases in children:
        ordinal = counts[child.name]
        counts[child.name] += 1
        child_scope = f"{scope}.{child.name}#{ordinal}"
        triggers: list[ast.expr | ast.type_param] = [
            *child.decorator_list,
            *child.bases,
            *(keyword.value for keyword in child.keywords),
            *child.type_params,
        ]
        child_blocker = fix_blocker or (
            _FixBlocker(
                "class bases, metaclass or decorators may inspect definition order",
                min(triggers, key=lambda node: (node.lineno, node.col_offset)),
            )
            if triggers
            else None
        )
        _body(result, child, child_scope, child_aliases, child_blocker)


def _analyze_order(
    tree: ast.Module,
    config: OrderingConfig,
    *,
    source: SourceFile | None = None,
    contract: str = "ordering",
    deferred: bool = False,
) -> FileOrder:
    result = FileOrder(
        source.path if source else "module.py",
        source.module if source else "module",
        tree,
        config,
        contract,
        deferred,
    )
    reflection = _reflective(tree)
    aliases = {
        name: f"builtins.{name}" for name in ("property", "staticmethod", "classmethod")
    }
    _body(result, tree, "module", aliases, reflection)
    return result
