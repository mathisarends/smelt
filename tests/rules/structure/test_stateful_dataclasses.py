from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT349",))
HEADER = (
    "from collections.abc import Iterable\n"
    "from dataclasses import dataclass, field\n"
    "from typing import Self\n\n\n"
)


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/activity.py": source},
    )


def test_reports_accumulating_dataclass_from_the_issue(tmp_path: Path) -> None:
    source = HEADER + (
        "@dataclass\n"
        "class InterruptedActivity:\n"
        '    """Activity a cancelled execution left open."""\n\n'
        "    turn: str | None = None\n"
        "    unanswered: dict[str, str] = field(default_factory=dict)\n"
        "    started: set[str] = field(default_factory=set)\n"
        "    batches: dict[str, str] = field(default_factory=dict)\n"
        "    broken_batches: set[str] = field(default_factory=set)\n\n"
        "    @classmethod\n"
        "    def scan(cls, events: Iterable[str]) -> Self:\n"
        "        activity = cls()\n"
        "        for event in events:\n"
        "            activity._observe(event)\n"
        "        return activity\n\n"
        "    def repairs(self) -> tuple[str, ...]:\n"
        "        return tuple(sorted(self.started))\n\n"
        "    def _observe(self, event: str) -> None:\n"
        "        match event.split(':'):\n"
        "            case ['turn', turn]:\n"
        "                self.turn = turn\n"
        "            case ['call', call, batch]:\n"
        "                self.batches[call] = batch\n"
        "                self.unanswered[call] = event\n"
        "            case ['start', call]:\n"
        "                self.started.add(call)\n"
        "            case ['done', call]:\n"
        "                self._break_batch(call)\n"
        "                self.unanswered.pop(call, None)\n\n"
        "    def _break_batch(self, call: str) -> None:\n"
        "        if call in self.batches:\n"
        "            self.broken_batches.add(self.batches[call])\n"
    )
    root = _project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT349",
        "app/activity.py",
        7,
        1,
    )
    assert finding.message == (
        "Dataclass InterruptedActivity mutates its own fields after construction "
        "in _observe, _break_batch"
    )
    assert finding.expected == {
        "class": "InterruptedActivity",
        "methods": ["_observe", "_break_batch"],
        "fields": ["batches", "broken_batches", "started", "turn", "unanswered"],
        "automatic_rewrite": False,
    }
    assert finding.hint == (
        "Build the values before constructing InterruptedActivity and make it "
        "frozen, or keep the accumulation in a plain class with private attributes."
    )
    assert (root / "app/activity.py").read_text() == source


@pytest.mark.parametrize(
    ("source", "fields"),
    [
        (
            "@dataclass\nclass Counter:\n    count: int = 0\n\n"
            "    def bump(self) -> None:\n        self.count += 1\n",
            ["count"],
        ),
        (
            "@dataclass\nclass Slot:\n    value: int | None = None\n\n"
            "    def clear(self) -> None:\n        del self.value\n",
            ["value"],
        ),
        (
            "@dataclass\nclass Pair:\n    left: int = 0\n    right: int = 0\n\n"
            "    def swap(this) -> None:\n"
            "        this.left, this.right = this.right, this.left\n",
            ["left", "right"],
        ),
        (
            "@dataclass\nclass Grid:\n    cells: list[list[int]]\n\n"
            "    def reset(self, row: int) -> None:\n        self.cells[row][0] = 0\n",
            ["cells"],
        ),
        (
            "import collections\nimport dataclasses\n\n"
            "@dataclasses.dataclass(slots=True, frozen=False)\nclass Log:\n"
            "    lines: collections.deque[str]\n\n"
            "    async def push(self, line: str) -> None:\n"
            "        self.lines.appendleft(line)\n",
            ["lines"],
        ),
        (
            "@dataclass\nclass Log:\n    lines: 'list[str]'\n\n"
            "    def push(self, line: str) -> None:\n"
            "        def write() -> None:\n            self.lines.append(line)\n"
            "        write()\n",
            ["lines"],
        ),
        (
            "@dataclass\nclass Bag:\n    items: object = field(default_factory=list)\n\n"
            "    def push(self, item: str) -> None:\n        self.items.append(item)\n",
            ["items"],
        ),
        (
            "@dataclass\nclass Settings:\n    name: str = ''\n\n"
            "    def rename(self, value: str) -> None:\n"
            "        setattr(self, 'name', value)\n",
            ["<setattr>"],
        ),
    ],
)
def test_reports_mutations_of_self(
    tmp_path: Path, source: str, fields: list[str]
) -> None:
    [finding] = violations(_project(tmp_path, HEADER + source), SELECT)
    assert finding.expected is not None
    assert finding.expected["fields"] == fields


@pytest.mark.parametrize(
    "source",
    [
        # Values derived from fields leave the instance unchanged.
        "@dataclass(frozen=True)\nclass Point:\n    x: int\n\n"
        "    def moved(self, dx: int) -> Point:\n"
        "        return Point(self.x + dx)\n",
        # __post_init__ completes construction.
        "@dataclass\nclass Span:\n    start: int\n    end: int = 0\n\n"
        "    def __post_init__(self) -> None:\n"
        "        self.end = max(self.end, self.start)\n",
        # Frozen dataclasses and an undecidable frozen flag.
        "@dataclass(frozen=True)\nclass Span:\n    tags: list[str]\n\n"
        "    def tag(self, value: str) -> None:\n        self.tags.append(value)\n",
        "FROZEN = True\n\n@dataclass(frozen=FROZEN)\nclass Span:\n    tags: list[str]\n\n"
        "    def tag(self, value: str) -> None:\n        self.tags.append(value)\n",
        # Plain classes may own mutable state.
        "class Buffer:\n    def __init__(self) -> None:\n        self._lines: list[str] = []\n\n"
        "    def push(self, line: str) -> None:\n        self._lines.append(line)\n",
        # Mutating a local copy or a collaborator does not mutate the instance.
        "@dataclass\nclass Report:\n    lines: tuple[str, ...]\n    sink: object\n\n"
        "    def render(self) -> list[str]:\n"
        "        lines = list(self.lines)\n        lines.append('end')\n"
        "        self.sink.update(lines)\n        return lines\n",
        # Static and class methods have no instance receiver.
        "@dataclass\nclass Registry:\n    names: list[str]\n\n"
        "    @staticmethod\n    def build(self: Registry) -> None:\n"
        "        self.names.append('x')\n\n"
        "    @classmethod\n    def empty(cls) -> Registry:\n"
        "        cls.names = []\n        return cls([])\n",
        # Writes to other instances are outside this class's lifecycle.
        "@dataclass\nclass Node:\n    value: int\n\n"
        "    def copy_to(self, other: Node) -> None:\n        other.value = self.value\n",
    ],
)
def test_accepts_value_like_dataclasses(tmp_path: Path, source: str) -> None:
    assert violations(_project(tmp_path, HEADER + source), SELECT) == []


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_activity.py": HEADER
            + "@dataclass\nclass Spy:\n    calls: list[str]\n\n"
            "    def record(self, call: str) -> None:\n        self.calls.append(call)\n",
        },
    )
    assert violations(root, SELECT) == []


def test_reasoned_suppression_keeps_intentional_mutable_record(
    tmp_path: Path,
) -> None:
    source = HEADER + (
        "@dataclass\n"
        "class Progress:  # smelt: ignore[SMT349] -- progress shared by workers\n"
        "    done: int = 0\n\n"
        "    def advance(self) -> None:\n        self.done += 1\n"
    )
    assert violations(_project(tmp_path, source), SELECT) == []


def test_rule_is_opt_in_and_uses_configured_severity(tmp_path: Path) -> None:
    source = HEADER + (
        "@dataclass\nclass Counter:\n    count: int = 0\n\n"
        "    def bump(self) -> None:\n        self.count += 1\n"
    )
    assert violations(_project(tmp_path / "default", source)) == []
    config = CONFIG + "rules: {stateful-dataclass: warning}\n"
    [finding] = violations(_project(tmp_path / "enabled", source, config=config))
    assert (finding.code, finding.severity) == ("SMT349", Severity.WARNING)
