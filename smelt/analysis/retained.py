"""Bounded, exclusively owned dependency state for serialized worker requests."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.member_state import MemberIndexState

type StateKey = tuple[str, str]


class RetainedAnalysis:
    def __init__(
        self, *, max_bytes: int = 8 * 1024 * 1024, max_entries: int = 2
    ) -> None:
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._states: OrderedDict[StateKey, tuple[MemberIndexState, int]] = (
            OrderedDict()
        )
        self._bytes = 0

    def take(self, key: StateKey) -> MemberIndexState | None:
        # Transfer ownership before mutation: a failed update cannot poison the
        # next request. Only completed states are put back into the session.
        item = self._states.pop(key, None)
        if item is None:
            return None
        state, size = item
        self._bytes -= size
        return state

    def put(self, key: StateKey, state: MemberIndexState, *, size: int) -> None:
        self.take(key)
        if size > self.max_bytes or self.max_entries < 1:
            return
        while self._states and (
            self._bytes + size > self.max_bytes or len(self._states) >= self.max_entries
        ):
            _, (_, previous_size) = self._states.popitem(last=False)
            self._bytes -= previous_size
        self._states[key] = state, size
        self._bytes += size


class _RequestState:
    def __init__(self, state: RetainedAnalysis) -> None:
        self.state = state
        self.taken: set[StateKey] = set()

    def take(self, key: StateKey) -> MemberIndexState | None:
        # Current and historical analyses can use the same disk key within one
        # request. They must never borrow the same live mutable dependency graph.
        if key in self.taken:
            return None
        self.taken.add(key)
        return self.state.take(key)

    def put(self, key: StateKey, state: MemberIndexState, *, size: int) -> None:
        self.state.put(key, state, size=size)


_CURRENT: ContextVar[_RequestState | None] = ContextVar(
    "retained_analysis", default=None
)


def current() -> _RequestState | None:
    return _CURRENT.get()


@contextmanager
def retain(state: RetainedAnalysis | None) -> Iterator[None]:
    token = _CURRENT.set(_RequestState(state) if state is not None else None)
    try:
        yield
    finally:
        _CURRENT.reset(token)
