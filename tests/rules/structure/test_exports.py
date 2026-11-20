from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("SMT330",))
_REEXPORT_OPTIONS = CheckOptions(select=("SMT368",))


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
        tmp_path,
        {"smelt.yml": CONFIG + "rules: {visibility/misplaced-dunder-all: error}\n"},
    )
    assert [v.code for v in violations(tmp_path)] == ["SMT330"]
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG
            + "style:\n  overrides:\n    legacy: {match: app, rules: {visibility/misplaced-dunder-all: off}}\n"
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


@pytest.mark.parametrize(
    ("source", "names", "line"),
    [
        (
            "from .models import Event as Event\nfrom .service import open_events as open_events\n",
            ["Event", "open_events"],
            1,
        ),
        (
            '"""Published events."""\nfrom __future__ import annotations\n'
            'from .models import OldEvent as Event, Snapshot\n__all__ = ("Snapshot", "Event")\n',
            ["Event", "Snapshot"],
            3,
        ),
        ('import events\n__all__ = ["events"]\n', ["events"], 1),
        ("import events as events\n", ["events"], 1),
    ],
)
def test_module_with_only_explicit_reexports_is_reported_once(
    tmp_path: Path, source: str, *, names: list[str], line: int
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": source,
            "app/events_api.py": source,
            "tests/test_events_api.py": source,
        },
    )

    [finding] = violations(tmp_path, _REEXPORT_OPTIONS)

    assert (finding.code, finding.path, finding.line) == (
        "SMT368",
        "app/events_api.py",
        line,
    )
    assert finding.severity is Severity.HINT
    assert finding.expected == {"reexported_names": names}


@pytest.mark.parametrize(
    "source",
    [
        "import registrations\n",
        "from .models import Event\n",
        "from .models import Event as Event\nimport registrations\n",
        'from .models import Event as Event, Snapshot as Snapshot\n__all__ = ["Event"]\n',
        "from .models import Event as Event\n__all__ = load_exports()\n",
        "from .models import *\n__all__ = ['Event']\n",
        "if enabled:\n    from .models import Event as Event\n",
        "from .models import Event as Event\nfrom .local import Event as Event\n",
        "from .hooks import __getattr__ as __getattr__\n",
        "from .models import Event as Event\n\ndef create_event():\n    return Event()\n",
    ],
)
def test_modules_with_behavior_or_uncertain_exports_are_valid(
    tmp_path: Path, source: str
) -> None:
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/events_api.py": source},
    )

    assert violations(tmp_path, _REEXPORT_OPTIONS) == []


def test_reexport_module_review_is_opt_in_and_accepts_a_compatibility_exception(
    tmp_path: Path,
) -> None:
    source = "from .models import Event as Event\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/events_api.py": source},
    )
    assert violations(tmp_path) == []

    write_project(
        tmp_path, {"smelt.yml": CONFIG + "rules: {layout/reexport-only-module: hint}\n"}
    )
    assert [finding.code for finding in violations(tmp_path)] == ["SMT368"]

    write_project(
        tmp_path,
        {
            "app/events_api.py": "# smelt: ignore-file[SMT368] -- Keep the published import path.\n"
            + source,
        },
    )
    assert violations(tmp_path) == []
