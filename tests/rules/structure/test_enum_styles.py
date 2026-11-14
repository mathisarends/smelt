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
        "project_style": "auto",
        "auto_enums": 0,
        "literal_enums": 0,
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


def _modules(tmp_path: Path, **modules: str) -> Path:
    files = {"smelt.yml": CONFIG, "app/__init__.py": ""}
    files.update({f"app/{name}.py": source for name, source in modules.items()})
    return write_project(tmp_path, files)


AUTO_ENUM = _enum("from enum import StrEnum, auto", "A = auto()", "B = auto()")
LITERAL_ENUM = _enum("from enum import StrEnum", 'A = "a"', 'B = "b"')
CUSTOM_ENUM = _enum("from enum import StrEnum", 'MOVED = "mouseMoved"')


def test_explicit_enum_in_auto_project_is_reported_across_modules(
    tmp_path: Path,
) -> None:
    root = _modules(tmp_path, first=AUTO_ENUM, second=AUTO_ENUM, third=LITERAL_ENUM)
    [finding] = violations(root, OPTIONS)
    assert finding.path == "app/third.py"
    assert finding.expected is not None
    assert (
        finding.expected["suggested_style"],
        finding.expected["auto_enums"],
        finding.expected["literal_enums"],
    ) == ("auto", 2, 1)
    assert "auto() derives" in finding.message
    assert finding.hint == "Use auto() for A, B."


def test_auto_enum_in_explicit_project_is_reported(tmp_path: Path) -> None:
    root = _modules(tmp_path, first=LITERAL_ENUM, second=LITERAL_ENUM, third=AUTO_ENUM)
    [finding] = violations(root, OPTIONS)
    assert finding.path == "app/third.py"
    assert finding.expected is not None
    assert finding.expected["project_style"] == "literal"
    assert finding.hint == 'Spell A = "a", B = "b" explicitly.'


def test_tie_prefers_auto(tmp_path: Path) -> None:
    [finding] = violations(_modules(tmp_path, a=AUTO_ENUM, b=LITERAL_ENUM), OPTIONS)
    assert finding.path == "app/b.py"


def test_custom_value_enums_neither_decide_nor_violate_the_style(
    tmp_path: Path,
) -> None:
    root = _modules(tmp_path, a=AUTO_ENUM, b=CUSTOM_ENUM, c=CUSTOM_ENUM)
    assert violations(root, OPTIONS) == []


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


def test_mixed_enum_follows_explicit_project_style(tmp_path: Path) -> None:
    root = _modules(tmp_path, a=LITERAL_ENUM, b=LITERAL_ENUM, signaling=SOURCE)
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["suggested_style"] == "literal"
    assert (
        finding.hint == 'Spell DESCRIPTION = "description", READY = "ready" explicitly.'
    )
