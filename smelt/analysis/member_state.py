"""A disposable dependency index for member decisions across CLI processes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from collections.abc import Callable

type NamespaceIdentity = tuple[str, str, bool] | None


@dataclass
class MemberSummary:
    identity: str
    namespaces: dict[str, NamespaceIdentity]
    contributions: dict[str, int]
    queries: list[str]
    has_members: bool
    decision: str = ""


@dataclass
class MemberIndexState:
    files: dict[str, MemberSummary] = field(default_factory=dict)
    namespaces: dict[str, NamespaceIdentity] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    readers: dict[str, set[str]] = field(default_factory=dict)
    resolvers: dict[str, set[str]] = field(default_factory=dict)


MEMBER_STATE = TypeAdapter(MemberIndexState)


def update_members(
    state: MemberIndexState,
    identities: dict[str, str],
    namespaces: dict[str, NamespaceIdentity],
    build: Callable[[str], MemberSummary],
    decision: Callable[[MemberSummary, dict[str, int]], str],
) -> MemberIndexState:
    """Replace changed contributions and invalidate their reverse dependencies.

    A state is one atomically stored value, never a shared mutable database. A
    concurrent writer can replace it with another snapshot; the next invocation
    compares the entire input manifest before reusing any decision.
    """
    changed = {
        path
        for path in state.files.keys() | identities.keys()
        if path not in state.files or state.files[path].identity != identities.get(path)
    }
    for name in state.namespaces.keys() | namespaces.keys():
        if state.namespaces.get(name) != namespaces.get(name):
            changed.update(state.resolvers.get(name, ()))

    affected = set(changed)
    differences: dict[str, int] = {}
    for path in sorted(changed):
        _remove_file(state, path, differences)
        if path not in identities:
            continue
        current = build(path)
        state.files[path] = current
        for name in current.namespaces:
            state.resolvers.setdefault(name, set()).add(path)
        for query in current.queries:
            state.readers.setdefault(query, set()).add(path)
        for query, count in current.contributions.items():
            differences[query] = differences.get(query, 0) + count

    for query, delta in differences.items():
        if delta:
            count = state.totals.get(query, 0) + delta
            if count:
                state.totals[query] = count
            else:
                state.totals.pop(query, None)
            affected.update(state.readers.get(query, ()))
    for path in sorted(affected & state.files.keys()):
        summary = state.files[path]
        summary.decision = decision(summary, state.totals)
    state.namespaces = namespaces
    return state


def _remove_file(
    state: MemberIndexState, path: str, differences: dict[str, int]
) -> None:
    previous = state.files.pop(path, None)
    if previous is None:
        return
    for name in previous.namespaces:
        _remove_reader(state.resolvers, name, path)
    for query in previous.queries:
        _remove_reader(state.readers, query, path)
    for query, count in previous.contributions.items():
        differences[query] = differences.get(query, 0) - count


def _remove_reader(readers: dict[str, set[str]], query: str, path: str) -> None:
    paths = readers[query]
    paths.discard(path)
    if not paths:
        del readers[query]
