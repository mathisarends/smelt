from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_policy_project

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
AUTO_CONFIG = CONFIG + "style: {str_enum_values: auto}\n"
SELECT = CheckOptions(select=("MOD004",))
HEADER = "from enum import StrEnum, auto"


def _enum(*members: str, header: str = HEADER, base: str = "StrEnum") -> str:
    body = "".join(f"    {member}\n" for member in members)
    return f"{header}\n\nclass Kind({base}):\n{body}"


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_policy_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/kinds.py": source},
    )


def _auto(tmp_path: Path, source: str) -> list[Violation]:
    return violations(_project(tmp_path, source, config=AUTO_CONFIG), SELECT)


def _explicit(tmp_path: Path, source: str) -> list[Violation]:
    return violations(_project(tmp_path, source), SELECT)


def test_auto_style_reports_derivable_strings(tmp_path: Path) -> None:
    source = _enum("OPEN = auto()", 'CLOSED = "closed"', 'READY = "ready"')
    root = _project(tmp_path, source, config=AUTO_CONFIG)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "MOD004",
        "app/kinds.py",
        3,
        1,
    )
    assert finding.expected == {
        "enum": "Kind",
        "members": ["CLOSED", "READY"],
        "str_enum_values": "auto",
        "automatic_rewrite": True,
    }
    assert finding.hint == "Use auto() for CLOSED, READY."
    assert (root / "app/kinds.py").read_text() == source


def test_auto_style_spells_auto_members_in_classes_with_custom_values(
    tmp_path: Path,
) -> None:
    source = _enum('MOVED = "mouseMoved"', 'WHEEL = "wheel"', "PRESSED = auto()")
    [finding] = _auto(tmp_path, source)
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
    assert _auto(tmp_path, _enum(*members)) == []


def test_explicit_style_reports_auto_members(tmp_path: Path) -> None:
    source = _enum("DESCRIPTION = auto()", 'CANDIDATE = "candidate"', "READY = auto()")
    [finding] = _explicit(tmp_path, source)
    assert finding.code == "MOD004"
    assert finding.expected == {
        "enum": "Kind",
        "members": ["DESCRIPTION", "READY"],
        "str_enum_values": "explicit",
        "automatic_rewrite": True,
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
    assert _explicit(tmp_path, _enum(*members)) == []


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
    assert len(_auto(tmp_path / "auto", source)) == 1
    assert len(_explicit(tmp_path / "explicit", source)) == 1


def test_non_str_enum_classes_are_not_reported(tmp_path: Path) -> None:
    source = _enum(
        "A = auto()", 'B = "b"', header="from enum import Enum, auto", base="Enum"
    )
    assert _auto(tmp_path / "auto", source) == []
    assert _explicit(tmp_path / "explicit", source) == []


def test_tests_are_not_checked(tmp_path: Path) -> None:
    for style, config in (("explicit", CONFIG), ("auto", AUTO_CONFIG)):
        root = write_policy_project(
            tmp_path / style,
            {
                "smelt.yml": config,
                "app/__init__.py": "",
                "tests/test_kinds.py": _enum("A = auto()", 'B = "b"'),
            },
        )
        assert violations(root, SELECT) == []


@pytest.mark.parametrize(
    ("base", "members"),
    [(AUTO_CONFIG, ('A = "a"',)), (CONFIG, ("A = auto()",))],
)
def test_rule_is_opt_in_and_uses_configured_severity(
    tmp_path: Path, base: str, members: tuple[str, ...]
) -> None:
    source = _enum(*members)
    # Setting str_enum_values enables the rule; the default convention does not.
    default = violations(_project(tmp_path / "default", source, config=base))
    assert [v.code for v in default] == (["MOD004"] if base == AUTO_CONFIG else [])
    config = base + "rules: {modeling/str-enum-value-convention: warning}\n"
    [finding] = violations(_project(tmp_path / "enabled", source, config=config))
    assert (finding.code, finding.severity) == ("MOD004", Severity.WARNING)
