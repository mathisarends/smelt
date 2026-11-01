from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\nrules: {internal-public-module-symbol: warning}\n"
SOURCE = "MIN_LENGTH = 8\ntype Payload = str | bytes\ndef _valid(value: Payload): return len(value) >= MIN_LENGTH\n"


def _project(root: Path, source: str = SOURCE, *, config: str = CONFIG) -> Path:
    return write_project(
        root,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/models.py": source,
        },
    )


def test_module_symbol_review_is_opt_in_and_supports_cli_selection(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, config="version: 1\nproject: {root_packages: [app]}\n")
    assert violations(root) == []
    assert [
        item.code for item in violations(root, CheckOptions(select=("SMT320",)))
    ] == ["SMT320", "SMT320"]


def test_findings_expose_scope_locations_and_collision_safe_names(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "_MIN_LENGTH = 2\n" + SOURCE)
    constant, alias = violations(root)
    assert (constant.code, constant.path, constant.line, constant.column) == (
        "SMT320",
        "app/models.py",
        2,
        1,
    )
    assert constant.expected is not None
    assert constant.expected["suggested_name"] == "_MIN_LENGTH_internal"
    assert constant.expected["automatic_rename"] is False
    assert (
        "external consumers and arbitrary dynamic access"
        in constant.expected["usage_scope"]["unassessed_consumers"]
    )
    assert alias.expected is not None
    assert alias.expected["kind"] == "type-alias"
    assert alias.expected["suggested_name"] == "_Payload"
    assert (root / "app/models.py").read_text() == "_MIN_LENGTH = 2\n" + SOURCE


@pytest.mark.parametrize(
    "extra",
    [
        {"app/caller.py": "from app.models import MIN_LENGTH, Payload"},
        {"app/caller.py": "import app.models as models\nconsume(models)"},
        {"app/caller.py": "from app.models import *"},
        {"tests/test_models.py": "from app.models import MIN_LENGTH, Payload"},
        {"app/__init__.py": "from .models import MIN_LENGTH, Payload as PublicPayload"},
        {
            "app/api.py": "from .models import MIN_LENGTH as LIMIT, Payload as Value",
            "app/caller.py": "from .api import LIMIT, Value",
        },
    ],
)
def test_imports_tests_facades_and_escaped_modules_preserve_names(
    tmp_path: Path, extra: dict[str, str]
) -> None:
    root = _project(tmp_path)
    write_project(root, extra)
    assert violations(root) == []


def test_exports_and_package_initializer_definitions_preserve_names(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "__all__ = ['MIN_LENGTH', 'Payload']\n" + SOURCE)
    write_project(root, {"app/__init__.py": SOURCE})
    assert violations(root) == []


def test_public_field_alias_is_retained_while_internal_constant_is_reviewed(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "MIN_LENGTH = 8\ntype SecretValue = str | bytes\nclass Secret:\n    value: SecretValue\n    def valid(self): return len(self.value) >= MIN_LENGTH\n",
    )
    [finding] = violations(root)
    assert finding.expected is not None
    assert finding.expected["member"] == "MIN_LENGTH"


def test_package_override_and_reasoned_suppression_are_respected(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "MIN_LENGTH = 8  # smelt: ignore[SMT320] -- Supported external constant.\n",
        config=CONFIG
        + "structure:\n  contracts:\n    legacy:\n      match: app.legacy\n      rules: {internal-public-module-symbol: off}\n",
    )
    write_project(root, {"app/legacy/models.py": SOURCE})
    assert violations(root) == []


def test_cache_tracks_added_removed_and_reexported_symbol_consumers(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    assert len(violations(root)) == 2
    for facade, caller, expected in [
        ("from .models import MIN_LENGTH, Payload as PublicPayload\n", "", 0),
        ("", "from app.models import MIN_LENGTH, Payload\n", 0),
        ("", "", 2),
    ]:
        write_project(root, {"app/__init__.py": facade, "app/caller.py": caller})
        cached = check(root).report
        uncached = check(root, CheckOptions(use_cache=False)).report
        assert render_json(cached) == render_json(uncached)
        assert len(cached.violations) == expected
    (root / "app/caller.py").unlink()
    assert len(violations(root)) == 2
