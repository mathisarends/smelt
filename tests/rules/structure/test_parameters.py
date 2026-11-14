from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

POLICY = """\
version: 1
project: {root_packages: [app]}
structure:
  contracts:
    signatures:
      match: app
      max_positional_parameters: {limit}
"""
SELECT = CheckOptions(select=("SMT345",))


def _project(tmp_path: Path, source: str, *, limit: int = 2) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": POLICY.replace("{limit}", str(limit)),
            "app/__init__.py": "",
            "app/browser.py": source,
        },
    )


def test_reports_parameters_beyond_the_limit(tmp_path: Path) -> None:
    source = "def connect(user, run, tab, retry):\n    return 1\n"
    root = _project(tmp_path, source)
    [finding] = violations(root)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT345",
        "app/browser.py",
        1,
        1,
    )
    assert finding.message == (
        "connect accepts 4 positional parameters; signatures allows 2"
    )
    assert finding.expected == {
        "contract": "signatures",
        "max_positional_parameters": 2,
        "function": "connect",
        "positional": ["user", "run", "tab", "retry"],
        "keyword_only_candidates": ["tab", "retry"],
        "automatic_rewrite": False,
    }
    assert finding.hint == (
        "Make tab, retry keyword-only by placing * after run; "
        "pass them by keyword at call sites."
    )
    assert (root / "app/browser.py").read_text() == source


@pytest.mark.parametrize(
    "source",
    [
        "def connect(user, run):\n    return 1\n",
        "def connect(user, run, *, tab, retry):\n    return 1\n",
        "def connect(user, *args, **kwargs):\n    return 1\n",
        "class Browser:\n    def connect(self, user, run):\n        return 1\n",
        "class Browser:\n    @classmethod\n    def connect(cls, user, run):\n        return 1\n",
        "class Browser:\n    def __exit__(self, kind, error, trace):\n        return None\n",
        "from typing import overload\n\n\n@overload\ndef connect(a, b, c) -> int: ...\n",
        "from typing import override\n\n\nclass Browser:\n    @override\n    def connect(self, a, b, c):\n        return 1\n",
    ],
)
def test_accepts_signatures_within_the_policy(tmp_path: Path, source: str) -> None:
    assert violations(_project(tmp_path, source)) == []


@pytest.mark.parametrize(
    ("source", "name", "positional"),
    [
        (
            "class Browser:\n    @staticmethod\n    def connect(user, run, tab):\n        return 1\n",
            "connect",
            ["user", "run", "tab"],
        ),
        (
            "class Browser:\n    async def connect(self, user, run, tab):\n        return 1\n",
            "connect",
            ["user", "run", "tab"],
        ),
        (
            "def connect(user, run, tab, /):\n    return 1\n",
            "connect",
            ["user", "run", "tab"],
        ),
        (
            "def outer():\n    def inner(a, b, c):\n        return a\n    return inner\n",
            "inner",
            ["a", "b", "c"],
        ),
        (
            "if True:\n    def connect(a, b, c):\n        return a\n",
            "connect",
            ["a", "b", "c"],
        ),
    ],
)
def test_reports_methods_static_nested_and_positional_only(
    tmp_path: Path, source: str, name: str, positional: list[str]
) -> None:
    [finding] = violations(_project(tmp_path, source))
    assert finding.expected is not None
    assert (finding.expected["function"], finding.expected["positional"]) == (
        name,
        positional,
    )


def test_zero_limit_requires_keyword_only_parameters(tmp_path: Path) -> None:
    source = "class Browser:\n    def tabs(self, user_id):\n        return []\n"
    [finding] = violations(_project(tmp_path, source, limit=0))
    assert finding.hint == (
        "Make user_id keyword-only by placing * before the first parameter; "
        "pass them by keyword at call sites."
    )


def test_inactive_without_policy(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/browser.py": "def connect(a, b, c):\n    return 1\n",
        },
    )
    assert violations(root, SELECT) == []


def test_closest_package_policy_wins(tmp_path: Path) -> None:
    config = POLICY.replace("{limit}", "2") + (
        "    legacy:\n      match: app.legacy\n      max_positional_parameters: 4\n"
    )
    root = write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/legacy/__init__.py": "",
            "app/legacy/client.py": "def connect(a, b, c, d):\n    return 1\n",
            "app/client.py": "def connect(a, b, c):\n    return 1\n",
        },
    )
    assert [finding.path for finding in violations(root)] == ["app/client.py"]


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": POLICY.replace("{limit}", "0"),
            "app/__init__.py": "",
            "tests/test_browser.py": "def test_tabs(tmp_path, monkeypatch):\n    assert tmp_path\n",
        },
    )
    assert violations(root) == []
