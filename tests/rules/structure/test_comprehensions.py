from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT363",))


@pytest.mark.parametrize(
    ("expression", "condition"),
    [
        ("[x for group in groups for x in group]", "too many for clauses"),
        ("{x for group in groups for x in group}", "too many for clauses"),
        ("(x async for group in groups for x in group)", "too many for clauses"),
        (
            "[convert(x) if x.ready else x for x in items]",
            "nontrivial conditional element",
        ),
        (
            "{x.id: convert(x) if x.ready else x for x in items}",
            "nontrivial conditional element",
        ),
        ("[x for x in items if x.ready if x.active]", "multiple or compound filters"),
        ("[x for x in items if a and b and c]", "multiple or compound filters"),
        ("[x for x in items if a or (b and c and d)]", "multiple or compound filters"),
    ],
)
def test_reports_named_conditions(
    tmp_path: Path, expression: str, condition: str
) -> None:
    source = f"value = {expression}\n"
    root = _project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT363",
        "app/model.py",
        1,
        9,
    )
    assert finding.severity == Severity.HINT
    assert finding.expected is not None
    assert {
        key: value
        for key, value in finding.expected.items()
        if key != "comprehension_edges"
    } == {
        "max_comprehension_loops": 1,
        "conditions": [condition],
        "automatic_rewrite": False,
    }
    assert condition in finding.message
    assert (root / "app/model.py").read_text() == source


@pytest.mark.parametrize(
    "expression",
    [
        "[x.name for x in items if x.enabled]",
        "[x if x is not None else default for x in items]",
        "[x.name if ready else None for x in items]",
        "{x.id: x.name if ready else fallback.name for x in items}",
        "(resolve(x) for x in items if a and b)",
        "[Response.model_validate(x, from_attributes=True) for x in items]",
    ],
)
def test_accepts_simple_mapping_filtering_and_fallbacks(
    tmp_path: Path, expression: str
) -> None:
    assert violations(_project(tmp_path, f"value = {expression}\n"), SELECT) == []


def test_groups_nested_triggers_at_the_outermost_comprehension(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "value = [[convert(x) if ready else x for group in groups for x in group if a if b] for groups in batches]\n",
    )
    [finding] = violations(root, SELECT)
    assert (finding.line, finding.column) == (1, 9)
    assert finding.expected is not None
    assert finding.expected["conditions"] == [
        "multiple or compound filters",
        "nontrivial conditional element",
        "too many for clauses",
    ]


def test_groups_an_inner_trigger_when_the_outer_comprehension_is_simple(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path, "value = [[x for g in groups for x in g] for groups in batches]\n"
    )
    [finding] = violations(root, SELECT)
    assert finding.column == 9


def test_global_limit_and_closest_package_override(tmp_path: Path) -> None:
    source = "value = [x for a in items for b in a for x in b]\n"
    root = _project(tmp_path, source)
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {complexity/excess-comprehension-loops: warning}
style:
  max_comprehension_loops: 3
  overrides:
    outer: {match: app, max_comprehension_loops: 2}
    inner: {match: app.quiet, max_comprehension_loops: 3}
""",
            "app/quiet/__init__.py": "",
            "app/quiet/model.py": source,
        },
    )
    [finding] = violations(root)
    assert (finding.path, finding.severity) == ("app/model.py", Severity.WARNING)
    assert finding.expected is not None
    assert finding.expected["max_comprehension_loops"] == 2
    (root / "smelt.yml").write_text(
        CONFIG
        + "rules: {complexity/excess-comprehension-loops: hint}\nstyle: {max_comprehension_loops: 3}\n",
        encoding="utf-8",
    )
    assert violations(root) == []


def test_explicit_selection_off_override_suppression_and_test_exclusion(
    tmp_path: Path,
) -> None:
    source = "value = [x for group in groups for x in group]\n"
    root = _project(tmp_path, source)
    (root / "smelt.yml").write_text(
        CONFIG
        + "style: {max_comprehension_loops: 1}\n"
        + "rules: {complexity/excess-comprehension-loops: off}\n",
        encoding="utf-8",
    )
    assert violations(root) == []
    (root / "smelt.yml").write_text(
        CONFIG + "style: {max_comprehension_loops: 1}\n", encoding="utf-8"
    )
    assert [v.code for v in violations(root)] == ["SMT363"]
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {complexity/excess-comprehension-loops: warning}
style:
  overrides:
    quiet: {match: app.quiet, rules: {complexity/excess-comprehension-loops: off}}
""",
            "app/quiet/__init__.py": source,
            "app/intentional.py": source.rstrip()
            + "  # smelt: ignore[SMT363] -- deliberate flattening\n",
            "tests/test_model.py": source,
        },
    )
    [finding] = violations(root)
    assert finding.path == "app/model.py"
    assert violations(root, CheckOptions(use_cache=False)) == violations(root)


@pytest.mark.parametrize(
    "setting",
    [
        "style: {max_comprehension_loops: 0}",
        "style: {overrides: {bad: {match: app, max_comprehension_loops: 0}}}",
    ],
)
def test_rejects_limits_below_one(tmp_path: Path, setting: str) -> None:
    root = _project(tmp_path, "")
    (root / "smelt.yml").write_text(CONFIG + setting, encoding="utf-8")
    with pytest.raises(ConfigError, match="max_comprehension_loops"):
        violations(root)


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "app/model.py": source,
        },
    )
