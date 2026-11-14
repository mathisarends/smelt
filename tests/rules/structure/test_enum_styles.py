from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
AUTO = CheckOptions(select=("SMT341",))
EXPLICIT = CheckOptions(select=("SMT342",))
HEADER = "from enum import StrEnum, auto"


def _enum(*members: str, header: str = HEADER, base: str = "StrEnum") -> str:
    body = "".join(f"    {member}\n" for member in members)
    return f"{header}\n\nclass Kind({base}):\n{body}"


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/kinds.py": source},
    )


def test_auto_style_reports_derivable_strings(tmp_path: Path) -> None:
    source = _enum("OPEN = auto()", 'CLOSED = "closed"', 'READY = "ready"')
    root = _project(tmp_path, source)
    [finding] = violations(root, AUTO)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT341",
        "app/kinds.py",
        3,
        1,
    )
    assert finding.expected == {
        "enum": "Kind",
        "members": ["CLOSED", "READY"],
        "automatic_rewrite": False,
    }
    assert finding.hint == "Use auto() for CLOSED, READY."
    assert (root / "app/kinds.py").read_text() == source


def test_auto_style_spells_auto_members_in_classes_with_custom_values(
    tmp_path: Path,
) -> None:
    source = _enum('MOVED = "mouseMoved"', 'WHEEL = "wheel"', "PRESSED = auto()")
    [finding] = violations(_project(tmp_path, source), AUTO)
    assert finding.expected is not None
    assert finding.expected["members"] == ["PRESSED"]
    assert finding.hint == 'Spell PRESSED = "pressed" explicitly.'


@pytest.mark.parametrize(
    "members",
    [
        ("A = auto()", "B = auto()"),
        ('MOVED = "mouseMoved"', 'WHEEL = "wheel"'),
        ("A = auto()", "B = A"),
        ("A = auto()", '_ignore_ = "a"'),
    ],
)
def test_auto_style_accepts_auto_and_custom_classes(
    tmp_path: Path, members: tuple[str, ...]
) -> None:
    assert violations(_project(tmp_path, _enum(*members)), AUTO) == []


def test_explicit_style_reports_auto_members(tmp_path: Path) -> None:
    source = _enum("DESCRIPTION = auto()", 'CANDIDATE = "candidate"', "READY = auto()")
    [finding] = violations(_project(tmp_path, source), EXPLICIT)
    assert finding.code == "SMT342"
    assert finding.expected == {
        "enum": "Kind",
        "members": ["DESCRIPTION", "READY"],
        "automatic_rewrite": False,
    }
    assert (
        finding.hint == 'Spell DESCRIPTION = "description", READY = "ready" explicitly.'
    )


@pytest.mark.parametrize(
    "members",
    [('A = "a"', 'B = "custom"'), ('A = "a"', "B = A")],
)
def test_explicit_style_accepts_explicit_classes(
    tmp_path: Path, members: tuple[str, ...]
) -> None:
    assert violations(_project(tmp_path, _enum(*members)), EXPLICIT) == []


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
    source = _enum(f"A = {auto}()", 'B: str = "b"', header=header, base=base)
    root = _project(tmp_path, source)
    assert len(violations(root, AUTO)) == 1
    assert len(violations(root, EXPLICIT)) == 1


def test_non_str_enum_classes_are_not_reported(tmp_path: Path) -> None:
    source = _enum(
        "A = auto()", 'B = "b"', header="from enum import Enum, auto", base="Enum"
    )
    root = _project(tmp_path, source)
    assert violations(root, AUTO) == violations(root, EXPLICIT) == []


def test_tests_are_not_checked(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_kinds.py": _enum("A = auto()", 'B = "b"'),
        },
    )
    assert violations(root, AUTO) == violations(root, EXPLICIT) == []


@pytest.mark.parametrize(
    ("name", "code", "members"),
    [
        ("str-enum-auto-values", "SMT341", ('A = "a"',)),
        ("str-enum-explicit-values", "SMT342", ("A = auto()",)),
    ],
)
def test_rules_are_opt_in_and_use_configured_severity(
    tmp_path: Path, name: str, code: str, members: tuple[str, ...]
) -> None:
    source = _enum(*members)
    assert violations(_project(tmp_path / "default", source)) == []
    config = CONFIG + f"rules: {{{name}: warning}}\n"
    [finding] = violations(_project(tmp_path / "enabled", source, config=config))
    assert (finding.code, finding.severity) == (code, Severity.WARNING)
