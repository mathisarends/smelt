from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT356",))


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/tabs.py": source},
    )


def _expressions(root: Path) -> list[tuple[str, list[str]]]:
    return [
        (item.expected["expression"], item.expected["methods"])
        for item in violations(root, SELECT)
        if item.expected is not None
    ]


def test_reports_attribute_narrowed_in_several_methods(tmp_path: Path) -> None:
    source = (
        "class Tabs:\n"
        "    def open(self, url):\n"
        "        assert self._stack is not None\n"
        "        self._stack.push(url)\n\n"
        "    async def close(self):\n"
        "        if self._stack:\n"
        "            assert self._stack is not None\n"
        "        self._stack.pop()\n\n"
        "    def focus(self):\n"
        "        assert self._stack is not None\n"
    )

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT356",
        "app/tabs.py",
        3,
        9,
    )
    assert finding.message == (
        "assert self._stack is not None repeats in 3 methods of Tabs: "
        "open, close, focus"
    )
    assert finding.expected == {
        "class": "Tabs",
        "expression": "self._stack is not None",
        "methods": ["open", "close", "focus"],
        "lines": [3, 8, 12],
        "automatic_rewrite": False,
    }


def test_covers_attribute_paths_isinstance_and_conjunctions(
    tmp_path: Path,
) -> None:
    source = (
        "class Plugin:\n"
        "    def start(this):\n"
        "        assert this._state.context is not None and this._page is not None\n\n"
        "    def stop(this):\n"
        "        assert this._state.context is not None\n"
        "        assert isinstance(this._page, Page)\n\n"
        "    def wait(this):\n"
        "        assert isinstance(this._page, Page)\n"
    )

    assert _expressions(_project(tmp_path, source)) == [
        ("this._state.context is not None", ["start", "stop"]),
        ("isinstance(this._page, Page)", ["stop", "wait"]),
    ]


def test_accepts_single_narrowings_and_other_asserts(tmp_path: Path) -> None:
    source = (
        "class Tabs:\n"
        "    def open(self, url):\n"
        "        assert self._stack is not None\n"
        "        assert self._stack is not None\n"
        "        assert url is not None\n\n"
        "    def close(self, other):\n"
        "        assert other._stack is not None\n"
        "        assert self._count > 0\n"
        "        assert self is not None\n\n"
        "    def focus(self):\n"
        "        assert self._count > 0\n"
    )

    assert _expressions(_project(tmp_path, source)) == []


def test_skips_nested_scopes_and_static_methods(tmp_path: Path) -> None:
    source = (
        "class Tabs:\n"
        "    def open(self):\n"
        "        assert self._stack is not None\n\n"
        "    def close(self):\n"
        "        def later():\n"
        "            assert self._stack is not None\n"
        "        return later\n\n"
        "    @staticmethod\n"
        "    def build(self):\n"
        "        assert self._stack is not None\n\n\n"
        "class Other:\n"
        "    def run(self):\n"
        "        assert self._stack is not None\n"
    )

    assert _expressions(_project(tmp_path, source)) == []


def test_ignores_tests(tmp_path: Path) -> None:
    source = (
        "class Case:\n"
        "    def a(self):\n        assert self.x is not None\n\n"
        "    def b(self):\n        assert self.x is not None\n"
    )
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_tabs.py": source,
        },
    )

    assert _expressions(root) == []


def test_is_opt_in(tmp_path: Path) -> None:
    source = (
        "class Tabs:\n"
        "    def a(self):\n        assert self.x is not None\n\n"
        "    def b(self):\n        assert self.x is not None\n"
    )

    assert [
        item for item in violations(_project(tmp_path, source)) if item.code == "SMT356"
    ] == []
    enabled = _project(
        tmp_path / "enabled",
        source,
        config=CONFIG + "rules: {idioms/duplicate-narrowing-assert: warning}\n",
    )
    assert [item.code for item in violations(enabled)].count("SMT356") == 1
