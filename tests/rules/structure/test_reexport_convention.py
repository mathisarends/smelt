from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_policy_project

if TYPE_CHECKING:
    from pathlib import Path

_CONFIG = "version: 2\nproject: {root_packages: [app], test_roots: [tests]}\n"
_SELECTED = CheckOptions(select=("SMT369",))


def test_all_reports_aliases_under_the_package_convention(tmp_path: Path) -> None:
    source = (
        "from .models import Event as Event, Other, NewEvent as OldEvent\n"
        "import collections as collections\n"
        "from .models import Snapshot as Snapshot\n"
        '__all__ = ["Event", "OldEvent", "Other", "collections"]\n'
    )
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG + "style: {reexports: all}\n",
            "app/__init__.py": source,
            "app/events.py": source,
            "tests/test_events.py": source,
        },
    )

    found = violations(tmp_path)

    assert codes_at(found) == [("SMT369", "app/__init__.py", n) for n in (1, 2, 3)]
    assert [v.expected for v in found] == [
        {"style": "all", "name": name} for name in ("Event", "collections", "Snapshot")
    ]
    assert all(v.severity is Severity.WARNING for v in found)


def test_alias_reports_direct_imports_exported_only_by_all(tmp_path: Path) -> None:
    source = (
        "from .models import Event, Snapshot as Snapshot, NewEvent as OldEvent\n"
        "import collections, xml.etree\n"
        "from .internal import Helper\n"
        "class Local: pass\n"
        '__all__ = ["Event", "Snapshot", "OldEvent", "collections", "xml", "Local"]\n'
    )
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG + "style: {reexports: alias}\n",
            "app/__init__.py": source,
        },
    )

    found = violations(tmp_path)

    assert codes_at(found) == [("SMT369", "app/__init__.py", n) for n in (1, 2)]
    assert [v.expected for v in found] == [
        {"style": "alias", "name": name} for name in ("Event", "collections")
    ]
    assert (tmp_path / "app/__init__.py").read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    ("style", "source"),
    [
        ("all", "if enabled:\n    from .models import Event as Event\n"),
        ("all", "from .models import Event as Event\nEvent = factory()\n"),
        ("all", "from .models import Event as Event\nfrom .legacy import *\n"),
        ("alias", "from .models import Event\n__all__ = runtime_exports()\n"),
        ("alias", "from .models import Event\nif enabled:\n    __all__ = ['Event']\n"),
        (
            "alias",
            "from .models import Event\nfrom .legacy import Event\n__all__ = ['Event']\n",
        ),
    ],
)
def test_uncertain_package_reexports_are_skipped(
    tmp_path: Path, style: str, source: str
) -> None:
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG + f"style: {{reexports: {style}}}\n",
            "app/__init__.py": source,
        },
    )

    assert violations(tmp_path) == []


def test_package_policy_switch_and_reasoned_allow_override(tmp_path: Path) -> None:
    config = (
        _CONFIG
        + """\
style:
  reexports: alias
  overrides:
    lists:
      match: app.lists
      reexports: all
    runtime:
      match: app.runtime
      reexports: {value: allow, reason: Preserve the list-only runtime API convention.}
"""
    )
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "from .events import Event\n__all__ = ['Event']\n",
            "app/lists/__init__.py": "from .events import Event as Event\n",
            "app/runtime/__init__.py": "from .events import Event\n__all__ = ['Event']\n",
        },
    )

    assert codes_at(violations(tmp_path)) == [
        ("SMT369", "app/__init__.py", 1),
        ("SMT369", "app/lists/__init__.py", 1),
    ]


def test_override_activates_only_its_package_and_off_wins(tmp_path: Path) -> None:
    source = "from .events import Event as Event\n"
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG,
            "app/__init__.py": source,
            "app/events/__init__.py": source,
        },
    )
    assert violations(tmp_path) == []
    assert len(violations(tmp_path, _SELECTED)) == 2

    config = (
        _CONFIG + "style: {overrides: {local: {match: app.events, reexports: all}}}\n"
    )
    write_policy_project(tmp_path, {"smelt.yml": config})
    assert codes_at(violations(tmp_path)) == [("SMT369", "app/events/__init__.py", 1)]

    write_policy_project(tmp_path, {"smelt.yml": config + "rules: {SMT369: off}\n"})
    assert violations(tmp_path, _SELECTED) == []
