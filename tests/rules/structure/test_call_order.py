from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT329",))


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path, {"smelt.yml": config, "app/__init__.py": "", "app/service.py": source}
    )


@pytest.mark.parametrize("prefix", ["", "_"])
def test_same_visibility_caller_after_callee_is_reviewed(
    tmp_path: Path, prefix: str
) -> None:
    source = (
        f"def {prefix}parse(): return 1\ndef {prefix}load(): return {prefix}parse()\n"
    )
    root = _project(tmp_path, source)
    [finding] = violations(root, OPTIONS)
    assert (finding.code, finding.line) == ("SMT329", 2)
    assert finding.expected is not None
    assert finding.expected["callees"] == [{"name": prefix + "parse", "line": 1}]
    assert (root / "app/service.py").read_text() == source


def test_ordinary_methods_and_async_calls_are_supported(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "class Reader:\n    async def parse(self): return 1\n    async def load(self): return await self.parse()\n",
    )
    assert [(v.code, v.line) for v in violations(root, OPTIONS)] == [("SMT329", 3)]


@pytest.mark.parametrize(
    "source",
    [
        "def load(): return parse()\ndef parse(): return 1\n",
        "def _parse(): return 1\ndef load(): return _parse()\n",
        "def parse(): return load()\ndef load(): return parse()\n",
        "def parse(): return 1\ndef load(parse): return parse()\n",
        "def parse(): return 1\ndef load(value=parse()): return parse()\n",
        "def parse(): return 1\ndef load():\n    def nested(): return parse()\n    return nested()\n",
        "class Reader(Parent):\n    def parse(self): return 1\n    def load(self): return self.parse()\n",
        "def parse(): return 1\nparse = external\ndef load(): return parse()\n",
        "@decorator\ndef parse(): return 1\ndef load(): return parse()\n",
    ],
)
def test_valid_or_uncertain_reading_orders_are_preserved(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_opt_in_override_and_reasoned_suppression(tmp_path: Path) -> None:
    source = "def parse(): return 1\ndef load(): return parse()\n"
    root = _project(tmp_path, source)
    assert violations(root) == []
    _project(tmp_path, source, CONFIG + "rules: {caller-before-callee: hint}\n")
    assert [v.code for v in violations(root)] == ["SMT329"]
    _project(
        tmp_path,
        source,
        CONFIG
        + "structure:\n  contracts:\n    legacy: {match: app, rules: {caller-before-callee: off}}\n",
    )
    assert violations(root, OPTIONS) == []
    _project(
        tmp_path,
        source.replace(
            "def load():",
            "def load():  # smelt: ignore[SMT329] -- Retains a published narrative order.\n   ",
        ),
    )
    assert violations(root, OPTIONS) == []
