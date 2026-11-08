from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.parsing import AstCache
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    import ast
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT338",))
SOURCE = """from typing import Literal
def first(button: Literal["left", "right"]): pass
def second(button: Literal["right", "left"]): pass
"""


def _project(tmp_path: Path, source: str = SOURCE, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/pointer.py": source,
        },
    )


def test_groups_the_pasted_pattern_without_losing_field_metadata(
    tmp_path: Path,
) -> None:
    source = """from typing import Annotated, Literal
from pydantic import Field
type ClickCount = Annotated[Literal[1, 2], Field(description="count")]
type ClickButton = Annotated[Literal["left", "right"], Field(description="button")]
def message(count: Literal[1, 2], button: Literal["left", "right"]) -> str:
    return button
"""
    root = _project(tmp_path, source)
    before = (root / "app/pointer.py").read_bytes()

    found = violations(root, OPTIONS)

    assert len(found) == 2
    assert {v.expected["literal"] for v in found if v.expected} == {
        "Literal[1, 2]",
        "Literal['left', 'right']",
    }
    assert [(v.line, v.column) for v in found] == [(3, 29), (4, 30)]
    for finding in found:
        assert finding.severity is Severity.HINT
        assert finding.expected is not None
        assert finding.expected["count"] == 2
        assert finding.expected["automatic_rewrite"] is False
        assert len(finding.expected["literal_edges"]) == 2
    assert (root / "app/pointer.py").read_bytes() == before


@pytest.mark.parametrize(
    ("imports", "annotation"),
    [
        ("from typing import Literal as L", "L[1, 2]"),
        ("import typing as t", "t.Literal[1, 2]"),
        ("import typing_extensions as t", "t.Literal[1, 2]"),
        ("from typing_extensions import Literal", "Literal[1, 2]"),
        ("from typing import Literal", '"Literal[1, 2]"'),
        ("from typing import Literal, Annotated", 'Annotated[Literal[1, 2], "meta"]'),
        ("from typing import Literal, Required", "Required[Literal[1, 2]]"),
        ("from typing import Literal", "list[Literal[1, 2]] | None"),
        ("from typing import Literal", "Literal[Literal[1], 2]"),
        ("from typing import Literal", "Literal[2, 1, 2]"),
        ("from typing import Literal", "Literal[-1, +2]"),
    ],
)
def test_supported_annotations(tmp_path: Path, imports: str, annotation: str) -> None:
    root = _project(tmp_path, f"{imports}\na: {annotation}\nb: {annotation}\n")

    [finding] = violations(root, OPTIONS)

    assert finding.expected is not None
    assert finding.expected["count"] == 2
    assert finding.line == 2


@pytest.mark.parametrize(
    "alias",
    [
        "type Button = Literal[1, 2]",
        "Button: TypeAlias = Literal[1, 2]",
        "Button = Literal[1, 2]",
    ],
)
def test_alias_definitions_count_but_alias_uses_do_not(
    tmp_path: Path, alias: str
) -> None:
    root = _project(
        tmp_path,
        f"from typing import Literal, TypeAlias\n{alias}\n"
        "def first(x: Button): pass\ndef second(x: Button): pass\n",
    )
    assert not violations(root, OPTIONS)
    write_project(
        root, {"app/other.py": "from typing import Literal\nx: Literal[1, 2]\n"}
    )

    found = violations(root, OPTIONS)

    assert len(found) == 2
    assert {v.expected["count"] for v in found if v.expected} == {2}


@pytest.mark.parametrize(
    "source",
    [
        "from typing import Literal\na: Literal[1]\nb: Literal[1]",
        "from typing import Literal\na: Literal[1, 1]\nb: Literal[1, 1]",
        "from typing import Literal\na: Literal[0, 1]\nb: Literal[False, True]",
        'from typing import Literal\na: Literal[1, 2]\nb: Literal["1", "2"]',
        "from typing import Literal, Annotated\na: Annotated[int, Literal[1, 2]]\nb: Annotated[int, Literal[1, 2]]",
        "from typing import Literal\na: Literal[Enum.A, Enum.B]\nb: Literal[Enum.A, Enum.B]",
        "from typing import Literal\na: Literal[build(), 2]\nb: Literal[build(), 2]",
        "from other import Literal\na: Literal[1, 2]\nb: Literal[1, 2]",
        "from .typing import Literal\na: Literal[1, 2]\nb: Literal[1, 2]",
        "from typing import Literal\nLiteral = object\na: Literal[1, 2]\nb: Literal[1, 2]",
        "from typing import Literal\ndef f(Literal): pass\na: Literal[1, 2]\nb: Literal[1, 2]",
        "import typing as t\nt.Literal = other\na: t.Literal[1, 2]\nb: t.Literal[1, 2]",
        "from typing import Literal\nfrom other import *\na: Literal[1, 2]\nb: Literal[1, 2]",
        'from typing import Literal\na: "not valid Python!"',
    ],
)
def test_nearby_valid_or_uncertain_cases(tmp_path: Path, source: str) -> None:
    assert not violations(_project(tmp_path, source), OPTIONS)


def test_does_not_group_separate_roots_or_test_files(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from typing import Literal\nx: Literal[1, 2]\n",
        "version: 1\nproject: {root_packages: [app, other]}\n",
    )
    write_project(
        root,
        {
            "other/__init__.py": "",
            "other/pointer.py": "from typing import Literal\nx: Literal[1, 2]\n",
            "tests/test_pointer.py": SOURCE,
        },
    )
    assert not violations(root, OPTIONS)


def test_opt_in_severity_package_overrides_and_suppressions(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert not violations(root)
    assert len(violations(root, CheckOptions(select=("SMT338",)))) == 1
    config = CONFIG + "rules: {duplicate-literal-type: warning}\n"
    (root / "smelt.yml").write_text(config, encoding="utf-8")
    assert violations(root)[0].severity is Severity.WARNING
    (root / "smelt.yml").write_text(
        config + "structure:\n  contracts:\n"
        "    local: {match: app, rules: {SMT338: off}}\n",
        encoding="utf-8",
    )
    assert not violations(root, OPTIONS)
    (root / "smelt.yml").write_text(config, encoding="utf-8")
    (root / "app/pointer.py").write_text(
        SOURCE.replace(
            'def first(button: Literal["left", "right"]): pass',
            'def first(button: Literal["left", "right"]): pass  # smelt: ignore[SMT338] -- Separate external contracts.',
        ),
        encoding="utf-8",
    )
    assert not violations(root)


def test_cache_reuses_facts_and_matches_fresh_results_after_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    baseline = violations(root, OPTIONS)
    original = AstCache.parse
    parsed: list[str] = []

    def track(cache: AstCache, path: str) -> ast.Module:
        parsed.append(path)
        return original(cache, path)

    monkeypatch.setattr(AstCache, "parse", track)
    assert violations(root, OPTIONS) == baseline
    assert "app/pointer.py" not in parsed
    for addition in (
        'from typing import Literal\nx: Literal["left", "right"]\n',
        'from typing import Literal\nx: Literal["up", "down"]\n',
        "",
    ):
        write_project(root, {"app/other.py": addition})
        assert violations(root, OPTIONS) == violations(
            root, replace(OPTIONS, use_cache=False)
        )
    (root / "app/other.py").unlink()
    (root / "app/pointer.py").rename(root / "app/renamed.py")
    assert violations(root, OPTIONS) == violations(
        root, replace(OPTIONS, use_cache=False)
    )


def test_debt_detects_growth_and_replacement_inside_an_existing_group(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, config=CONFIG + "debt: debt.json\n")
    outcome = check(root, OPTIONS)
    Debt.from_violations(outcome.unfiltered, snippet_reader(outcome.context)).write(
        root / "debt.json"
    )
    assert not violations(root, OPTIONS)
    # Moving an accepted group does not create a new semantic occurrence.
    (root / "app/pointer.py").write_text("\n\n" + SOURCE, encoding="utf-8")
    assert not violations(root, OPTIONS)
    for changed in (
        SOURCE + 'def third(button: Literal["left", "right"]): pass\n',
        SOURCE.replace("def second", "def replacement"),
    ):
        (root / "app/pointer.py").write_text(changed, encoding="utf-8")
        assert len(violations(root, OPTIONS)) == 1
    (root / "app/pointer.py").write_text(
        SOURCE.split("def second", maxsplit=1)[0], encoding="utf-8"
    )
    assert not violations(root, OPTIONS)


def test_related_locations_are_bounded_for_large_groups(tmp_path: Path) -> None:
    source = "from typing import Literal\n" + "\n".join(
        f"def operation_{index}(button: Literal[1, 2]): pass" for index in range(20)
    )
    [finding] = violations(_project(tmp_path, source), OPTIONS)
    assert finding.expected is not None
    assert finding.expected["count"] == 20
    assert len(finding.expected["locations"]) == 5
    assert finding.expected["locations_truncated"] is True
