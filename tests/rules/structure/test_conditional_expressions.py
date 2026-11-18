from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT359",))


@pytest.mark.parametrize(
    "expression",
    [
        "first if a else second if b else third if c else last",
        "(first if a else second) if b else last",
        "(first if a else second) if b else (third if c else last)",
        "(\n    first if a\n    else second if b\n    else last\n)",
    ],
)
def test_reports_once_at_outermost_direct_nesting(
    tmp_path: Path, expression: str
) -> None:
    root = _project(tmp_path, f"value = {expression}\n")

    (finding,) = violations(root, SELECT)

    assert (finding.code, finding.path) == ("SMT359", "app/model.py")
    assert finding.column == (5 if expression.startswith("(\n") else 9)
    assert finding.line == (2 if expression.startswith("(\n") else 1)
    assert finding.severity == Severity.HINT
    assert finding.expected == {"automatic_rewrite": False}


@pytest.mark.parametrize(
    "expression",
    [
        "supplied if supplied is not None else default",
        "(a if x else b, c if y else d)",
        "(lambda: a if x else b) if y else default",
        "[a if x else b for x in items] if ready else []",
        "call(a if x else b) if ready else default",
        "a if (x if y else z) else b",
    ],
)
def test_accepts_separate_simple_conditionals(tmp_path: Path, expression: str) -> None:
    assert violations(_project(tmp_path, f"value = {expression}\n"), SELECT) == []


def test_reports_independent_nested_expression_inside_lambda(tmp_path: Path) -> None:
    root = _project(
        tmp_path, "value = (lambda: a if x else b if y else c) if z else d\n"
    )

    (finding,) = violations(root, SELECT)

    assert (finding.line, finding.column) == (1, 18)


def test_supports_opt_in_package_overrides_and_suppression(tmp_path: Path) -> None:
    source = "value = a if x else b if y else c\n"
    root = _project(tmp_path, source)
    assert not any(item.code == "SMT359" for item in violations(root))

    (root / "smelt.yml").write_text(
        CONFIG
        + "rules: {complexity/nested-conditional-expression: warning}\n"
        + "style:\n  overrides:\n    quiet:\n      match: app.quiet\n"
        + "      rules: {complexity/nested-conditional-expression: off}\n",
        encoding="utf-8",
    )
    write_project(
        root,
        {
            "app/quiet/__init__.py": source,
            "app/intentional.py": source.rstrip()
            + "  # smelt: ignore[SMT359] -- preserve compact generated dispatch\n",
        },
    )

    (finding,) = violations(root)
    assert (finding.code, finding.path, finding.severity) == (
        "SMT359",
        "app/model.py",
        Severity.WARNING,
    )
    assert violations(root, CheckOptions(use_cache=False)) == violations(root)


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/model.py": source},
    )
