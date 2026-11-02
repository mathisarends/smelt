from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("SMT330",))


@pytest.mark.parametrize(
    "source",
    [
        '__all__ = ["run"]\n',
        "__all__: list[str] = []\n",
        '__all__ += ["run"]\n',
        "if enabled:\n    __all__ = []\n",
        "from .api import __all__\n",
        "from .api import exports as __all__\n",
        "(__all__, other) = [], 1\n",
    ],
)
def test_module_export_declarations_are_reported_without_rewriting(
    tmp_path: Path, source: str
) -> None:
    write_project(
        tmp_path, {"smelt.yml": CONFIG, "app/__init__.py": "", "app/api.py": source}
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.code == "SMT330"
    assert finding.path == "app/api.py"
    assert finding.line == (2 if source.startswith("if ") else 1)
    assert (tmp_path / "app/api.py").read_text() == source


def test_package_exports_local_names_and_tests_remain_valid(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": '__all__ = ["run"]\n',
            "app/api.py": "class Options:\n    __all__ = []\ndef run():\n    __all__ = []\n",
            "tests/test_api.py": "__all__ = []\n",
        },
    )
    assert violations(tmp_path, OPTIONS) == []


def test_rule_is_opt_in_and_closest_off_wins(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/api.py": "__all__ = []\n"},
    )
    assert violations(tmp_path) == []
    write_project(
        tmp_path, {"smelt.yml": CONFIG + "rules: {package-only-dunder-all: error}\n"}
    )
    assert [v.code for v in violations(tmp_path)] == ["SMT330"]
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG
            + "structure:\n  contracts:\n    legacy: {match: app, rules: {package-only-dunder-all: off}}\n"
        },
    )
    assert violations(tmp_path, OPTIONS) == []


def test_reasoned_suppression_is_supported(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "app/api.py": "__all__ = []  # smelt: ignore[SMT330] -- Retain published star-import behavior.\n",
        },
    )
    assert violations(tmp_path, OPTIONS) == []
