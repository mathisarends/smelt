from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from smelt.analysis import identity
from smelt.analysis.identity import IdentityScope, analyzer_identity
from smelt.rules import identities
from smelt.rules.identities import implementation_identity
from tests.helpers import write_project

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from smelt.rules.base import RuleContract

A = "smelt.rules.structure.a"
B = "smelt.rules.structure.b"
META = "smelt.rules.meta"


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An editable analyzer whose rule ``a`` imports a helper and rule ``b`` does not."""
    root = write_project(
        tmp_path / "smelt",
        {
            "analysis/identity.py": "",
            "engine/check.py": "LIMIT = 1\n",
            "cli/app.py": "LIMIT = 1\n",
            "rules/__init__.py": "",
            "rules/base.py": "LIMIT = 1\n",
            "rules/meta.py": "from smelt.rules import base\n",
            "rules/catalog.json": "[]\n",
            "rules/structure/__init__.py": "",
            "rules/structure/a.py": "def check():\n    from .helper import LIMIT\n",
            "rules/structure/b.py": "from smelt.rules.base import LIMIT\n",
            "rules/structure/helper.py": "LIMIT = 1\n",
        },
    )
    monkeypatch.setattr(identity, "__file__", str(root / "analysis/identity.py"))
    monkeypatch.setattr(identities, "__file__", str(root / "rules/identities.py"))
    analyzer_identity.cache_clear()
    yield root
    analyzer_identity.cache_clear()


def _identity(module: str) -> str:
    analyzer_identity.cache_clear()
    rule = cast("RuleContract", SimpleNamespace(implementation_module=module))
    return implementation_identity(rule, None)


def _edit(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")


def test_rule_edit_changes_only_that_rule(package: Path) -> None:
    before = {module: _identity(module) for module in (A, B, META)}

    _edit(package / "rules/structure/a.py")

    assert _identity(A) != before[A]
    assert _identity(B) == before[B]
    assert _identity(META) == before[META]


def test_rule_module_imported_inside_a_function_belongs_to_its_importer(
    package: Path,
) -> None:
    before = {module: _identity(module) for module in (A, B)}

    _edit(package / "rules/structure/helper.py")

    assert _identity(A) != before[A]
    assert _identity(B) == before[B]


@pytest.mark.parametrize(
    "shared",
    [
        "rules/base.py",
        "rules/meta.py",
        "rules/catalog.json",
        "engine/check.py",
    ],
)
def test_shared_source_edit_changes_every_rule(package: Path, shared: str) -> None:
    before = {module: _identity(module) for module in (A, B, META)}

    _edit(package / shared)

    assert [_identity(module) != before[module] for module in (A, B, META)] == [
        True,
        True,
        True,
    ]


def test_package_edit_changes_the_rules_below_it(package: Path) -> None:
    before = {module: _identity(module) for module in (A, B, META)}

    _edit(package / "rules/structure/__init__.py")

    assert _identity(A) != before[A]
    assert _identity(B) != before[B]
    assert _identity(META) == before[META]


def test_cli_edit_keeps_every_rule(package: Path) -> None:
    before = {module: _identity(module) for module in (A, B, META)}

    _edit(package / "cli/app.py")

    assert {module: _identity(module) for module in before} == before


def test_rule_outside_the_package_uses_the_decision_identity(package: Path) -> None:
    assert _identity("tests.rules.custom") == analyzer_identity(IdentityScope.DECISIONS)


def test_installed_wheel_has_one_identity_without_reading_rule_sources(
    package: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identities, "hashes_sources", lambda: False)
    (package / "rules/catalog.json").unlink()

    assert _identity(A) == _identity(B) == analyzer_identity(IdentityScope.DECISIONS)
