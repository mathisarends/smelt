from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT347",))
HEADER = "from uuid import UUID\n\n\n"


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/browser.py": source},
    )


def test_reports_dict_value_tuple_from_the_issue(tmp_path: Path) -> None:
    source = HEADER + (
        "class Registry:\n"
        "    def __init__(self) -> None:\n"
        "        self._connections: dict[UUID, tuple[UUID, UUID, str]] = {}\n"
    )
    root = _project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT347",
        "app/browser.py",
        6,
        39,
    )
    assert finding.message == (
        "tuple[UUID, UUID, str] leaves its fields unnamed (repeated element type)"
    )
    assert finding.expected == {
        "tuple": "tuple[UUID, UUID, str]",
        "elements": ["UUID", "UUID", "str"],
        "repeated": ["UUID"],
        "automatic_rewrite": False,
    }
    assert finding.hint == "Name the fields with a NamedTuple or frozen dataclass."
    assert (root / "app/browser.py").read_text() == source


@pytest.mark.parametrize(
    ("source", "spelled"),
    [
        ("def route(pair: tuple[UUID, UUID]) -> None: ...\n", "tuple[UUID, UUID]"),
        ("def route() -> tuple[int, str, bytes]: ...\n", "tuple[int, str, bytes]"),
        ("def route(*pairs: tuple[int, int]) -> None: ...\n", "tuple[int, int]"),
        ("ROUTES: list[tuple[str, str]] = []\n", "tuple[str, str]"),
        ("type Route = tuple[UUID, UUID]\n", "tuple[UUID, UUID]"),
        (
            "from typing import TypeAlias\n\nRoute: TypeAlias = tuple[UUID, UUID]\n",
            "tuple[UUID, UUID]",
        ),
        (
            "from typing import Tuple\n\nROUTE: Tuple[int, int] = (1, 2)\n",
            "Tuple[int, int]",
        ),
        (
            "def route() -> tuple[tuple[int, int], str]: ...\n",
            "tuple[int, int]",
        ),
    ],
)
def test_reports_annotation_positions(
    tmp_path: Path, source: str, spelled: str
) -> None:
    [finding] = violations(_project(tmp_path, HEADER + source), SELECT)
    assert finding.expected is not None
    assert finding.expected["tuple"] == spelled


@pytest.mark.parametrize(
    "source",
    [
        "def route(pair: tuple[UUID, str]) -> None: ...\n",
        "def route(ids: tuple[UUID, ...]) -> None: ...\n",
        "def route() -> tuple[()]: ...\n",
        "def route(pair: 'tuple[UUID, UUID]') -> None: ...\n",
        "ROUTE = (1, 2, 3)\n",
        "PAIRS = [tuple[int, int]]\n",
        "class Grid:\n    def __getitem__(self, key: tuple[int, int]) -> int: ...\n",
        "from typing import override\n\n\nclass Grid:\n    @override\n    def get(self, key: tuple[int, int]) -> int: ...\n",
    ],
)
def test_accepts_named_variadic_and_dictated_shapes(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, HEADER + source), SELECT) == []


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_browser.py": "PAIR: tuple[int, int] = (1, 2)\n",
        },
    )
    assert violations(root, SELECT) == []
