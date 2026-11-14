from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT341",))
SOURCE = """\
from enum import StrEnum, auto

class SignalingMessageType(StrEnum):
    DESCRIPTION = auto()
    CANDIDATE = "candidate"
    READY = auto()
"""


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/signaling.py": source},
    )


def _enum(header: str, *members: str) -> str:
    body = "".join(f"    {member}\n" for member in members)
    return f"{header}\n\nclass Kind(StrEnum):\n{body}"


def test_mixed_enum_with_derivable_literals_suggests_auto(tmp_path: Path) -> None:
    root = _project(tmp_path, SOURCE)
    [finding] = violations(root, OPTIONS)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT341",
        "app/signaling.py",
        3,
        1,
    )
    assert finding.expected == {
        "enum": "SignalingMessageType",
        "auto_members": ["DESCRIPTION", "READY"],
        "literal_members": ["CANDIDATE"],
        "suggested_style": "auto",
        "automatic_rewrite": False,
    }
    assert finding.hint is not None
    assert "auto() for CANDIDATE" in finding.hint
    assert (root / "app/signaling.py").read_text() == SOURCE


def test_mixed_enum_with_custom_literals_suggests_explicit_strings(
    tmp_path: Path,
) -> None:
    source = _enum(
        "from enum import StrEnum, auto",
        'MOVED = "mouseMoved"',
        "WHEEL = auto()",
    )
    [finding] = violations(_project(tmp_path, source), OPTIONS)
    assert finding.expected is not None
    assert finding.expected["suggested_style"] == "literal"
    assert finding.hint is not None
    assert 'WHEEL = "wheel"' in finding.hint


@pytest.mark.parametrize(
    ("header", "base", "auto"),
    [
        ("from enum import StrEnum as Base, auto as derive", "Base", "derive"),
        ("import enum", "enum.StrEnum", "enum.auto"),
        ("import enum as e", "e.StrEnum", "e.auto"),
    ],
)
def test_import_spellings_are_recognized(
    tmp_path: Path, header: str, base: str, auto: str
) -> None:
    source = f'{header}\n\nclass Kind({base}):\n    A = {auto}()\n    B: str = "b"\n'
    assert len(violations(_project(tmp_path, source), OPTIONS)) == 1


@pytest.mark.parametrize(
    "members",
    [
        ("A = auto()", "B = auto()"),
        ('A = "a"', 'B = "custom"'),
        ("A = auto()", "B = A"),
        ("A = auto()", '_private = "x"'),
    ],
)
def test_consistent_enums_are_not_reported(
    tmp_path: Path, members: tuple[str, ...]
) -> None:
    source = _enum("from enum import StrEnum, auto", *members)
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_non_str_enum_classes_are_not_reported(tmp_path: Path) -> None:
    source = (
        "from enum import Enum, auto\n\nclass Kind(Enum):\n"
        "    A = auto()\n"
        '    B = "b"\n'
    )
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_tests_are_not_checked(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_signaling.py": SOURCE,
        },
    )
    assert violations(root, OPTIONS) == []


def test_rule_is_opt_in_and_uses_configured_severity(tmp_path: Path) -> None:
    assert violations(_project(tmp_path / "default", SOURCE)) == []
    config = CONFIG + "rules: {mixed-str-enum-values: warning}\n"
    [finding] = violations(_project(tmp_path / "enabled", SOURCE, config=config))
    assert (finding.code, finding.severity) == ("SMT341", Severity.WARNING)
