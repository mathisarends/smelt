from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import load_config
from smelt.diagnostics.violation import Severity
from smelt.engine.guide import (
    Guide,
    build_guide,
    render_guide,
    resolve_guide_target,
)
from smelt.engine.native_core import project_context
from tests.helpers import add_rules, write_policy_project

if TYPE_CHECKING:
    from pathlib import Path

POLICY = """\
version: 2
project: {root_packages: [app]}
architecture:
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
style:
  dataclass_options: [slots, frozen]
  reexports: alias
  overrides:
    domain:
      match: app.domain
      rules: {complexity/excess-positional-parameters: {severity: warning, max: 1}}
tests: {layout: mirror}
"""

SOURCES = {
    "app/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/domain/order.py": "",
    "app/application/__init__.py": "",
    "tests/__init__.py": "",
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = write_policy_project(tmp_path, {"smelt.yml": POLICY, **SOURCES})
    add_rules(
        root,
        {
            "complexity/excess-positional-parameters": "warning",
            "tests/missing-outcome-check": "warning",
            "typing/anonymous-tuple-type": "hint",
        },
    )
    return root


def _guide(root: Path, raw: str | None) -> Guide:
    loaded = load_config(root / "smelt.yml")
    ctx = project_context(loaded)
    return build_guide(ctx, resolve_guide_target(ctx, raw), loaded)


def _instructions(guide: Guide, *, tests: bool = False) -> dict[str, str]:
    return {
        entry.code: entry.instruction for entry in guide.entries if entry.tests is tests
    }


def test_planned_module_guide_renders_the_governing_policy(project: Path) -> None:
    guide = _guide(project, "app/application/new_service.py")

    instructions = _instructions(guide)
    assert guide.layer == "application"
    assert instructions["ARC001"] == (
        "From layer application, import only domain; reach other layers through a port"
    )
    assert instructions["CPX003"] == (
        "Accept at most 5 positional parameters; make the rest keyword-only with *"
    )
    assert (
        instructions["MOD006"]
        == "Declare dataclasses as @dataclass(slots=True, frozen=True)"
    )
    assert instructions["VIS008"] == (
        "Re-export in __init__.py with same-name aliases (from .x import A as A)"
    )


def test_package_override_changes_the_instruction_for_its_modules(
    project: Path,
) -> None:
    instructions = _instructions(_guide(project, "app/domain/order.py"))

    assert instructions["CPX003"] == (
        "Accept at most 1 positional parameter; make the rest keyword-only with *"
    )
    assert instructions["ARC001"] == "From layer domain, import no other layer"


def test_guide_lists_only_active_rules_that_apply(project: Path) -> None:
    guide = _guide(project, "app/application/new_service.py")

    codes = {entry.code for entry in guide.entries}
    severities = {entry.code: entry.severity for entry in guide.entries}
    assert severities["TYP007"] is Severity.HINT
    # Not enabled, only for feature code, and only with a match policy.
    assert codes.isdisjoint({"TYP008", "ARC011", "IDM002"})


def test_source_guide_includes_the_mirrored_test_rules(project: Path) -> None:
    guide = _guide(project, "app/application/new_service.py")

    assert guide.test_path == "tests/application/test_new_service.py"
    assert _instructions(guide, tests=True)["TST006"] == (
        "Assert a concrete outcome in every test"
    )
    assert "TST006" not in _instructions(guide)


def test_test_file_guide_lists_only_test_rules(project: Path) -> None:
    guide = _guide(project, "tests/application/test_new_service.py")

    assert guide.target.kind == "test"
    assert guide.target.planned
    assert guide.entries
    assert all(entry.tests for entry in guide.entries)


@pytest.mark.parametrize(
    ("python_version", "code", "instruction"),
    [
        ("3.14", "TYP001", "Never write from __future__ import annotations"),
        (
            "3.12",
            "TYP002",
            "Write from __future__ import annotations only when an annotation "
            "needs postponing",
        ),
    ],
)
def test_future_annotation_guide_follows_the_python_target(
    tmp_path: Path, python_version: str, code: str, instruction: str
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\n"
            f'project: {{root_packages: [app], python_version: "{python_version}"}}\n'
            "style: {future_annotations: when-needed}\n",
            "app/__init__.py": "",
        },
    )

    instructions = _instructions(_guide(root, "app/new.py"))

    assert instructions[code] == instruction
    assert {"TYP001", "TYP002"} - {code} & instructions.keys() == set()


def test_rendered_guide_groups_instructions_by_severity(project: Path) -> None:
    rendered = render_guide(_guide(project, "app/application/new_service.py"))

    assert rendered.startswith(
        "Guide: app.application.new_service (app/application/new_service.py) "
        "[planned]\nLayer: application\n"
    )
    required, expected = rendered.index("Required:"), rendered.index("Expected:")
    review = rendered.index("Review (hints")
    tests = rendered.index("Tests (tests/application/test_new_service.py)")
    assert required < expected < review < tests
    assert "  ARC001  From layer application, import only domain;" in rendered
