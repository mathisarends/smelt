from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.context import AnalysisContext
from smelt.analysis.python_version import minimum_from_requires_python
from smelt.config import load_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("spec", "minimum", "deferred"),
    [
        (">=3.14", (3, 14), True),
        (">=3.12,<4", (3, 12), False),
        ("~=3.14", (3, 14), True),
        ("==3.14.*", (3, 14), True),
        (">3.13", (3, 13), False),
        (">=3.14.2,!=3.14.3", (3, 14), True),
        (">=3.12,!=3.12.*,!=3.13.*", (3, 12), False),
        ("==3.*", (3, 0), False),
        ("<4", None, False),
        ("not a constraint", None, False),
        (">=3.14rc1", None, False),
        ("===custom", None, False),
    ],
)
def test_validated_lower_bounds(
    spec: str, minimum: tuple[int, int] | None, deferred: bool
) -> None:
    target = minimum_from_requires_python(spec, "pyproject.toml")

    assert target.minimum == minimum
    assert target.deferred_annotations is deferred
    assert target.source == "pyproject.toml"


def _context(tmp_path: Path, project: str = "") -> AnalysisContext:
    write_project(
        tmp_path,
        {
            "smelt.yaml": f"version: 1\nproject:\n  root_packages: [app]\n{project}",
            "app/main.py": "",
        },
    )
    return AnalysisContext(tmp_path, load_config(tmp_path / "smelt.yaml").config)


def test_unknown_does_not_infer_running_interpreter_or_pin(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    write_project(tmp_path, {".python-version": "3.14\n"})

    assert ctx.python_targets.for_path(tmp_path / "app/main.py").minimum is None


def test_explicit_minimum_overrides_metadata(tmp_path: Path) -> None:
    ctx = _context(tmp_path, '  python_version: "3.13"\n')
    write_project(
        tmp_path, {"pyproject.toml": '[project]\nrequires-python = ">=3.14"\n'}
    )

    target = ctx.python_targets.for_path(tmp_path / "app/main.py")

    assert target.minimum == (3, 13)
    assert target.source == "project.python_version"


@pytest.mark.parametrize("root_first", [True, False])
def test_nearest_project_owns_runtime_target(tmp_path: Path, root_first: bool) -> None:
    ctx = _context(tmp_path)
    write_project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nrequires-python = ">=3.14"\n',
            "member/pyproject.toml": '[project]\nrequires-python = ">=3.12"\n',
        },
    )
    if root_first:
        assert ctx.python_targets.for_path().minimum == (3, 14)

    target = ctx.python_targets.for_path(tmp_path / "member/src/client/api.py")

    assert target.minimum == (3, 12)
    assert target.source == "member/pyproject.toml"
    assert ctx.python_targets.for_path().minimum == (3, 14)


@pytest.mark.parametrize(
    "metadata",
    [
        "broken = [",
        '[project]\nname = "independent"\n',
        '[project]\nrequires-python = "invalid"\n',
    ],
)
def test_uncertain_member_does_not_inherit_root_minimum(
    tmp_path: Path, metadata: str
) -> None:
    ctx = _context(tmp_path)
    write_project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nrequires-python = ">=3.14"\n',
            "member/pyproject.toml": metadata,
        },
    )

    target = ctx.python_targets.for_path(tmp_path / "member/code.py")

    assert target.minimum is None
    assert target.uncertainty is not None


def test_external_source_root_uses_its_own_metadata(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "policy/smelt.yaml": "version: 1\nproject: {root_packages: [app], source_roots: [../member/src]}\n",
            "policy/pyproject.toml": '[project]\nrequires-python = ">=3.14"\n',
            "member/pyproject.toml": '[project]\nrequires-python = ">=3.12"\n',
            "member/src/app/module.py": "",
        },
    )
    ctx = AnalysisContext(
        tmp_path / "policy", load_config(tmp_path / "policy/smelt.yaml").config
    )

    target = ctx.python_targets.for_path(
        tmp_path / "policy/../member/src/app/planned.py"
    )

    assert target.minimum == (3, 12)


def test_tool_only_metadata_does_not_hide_project_minimum(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    write_project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nrequires-python = ">=3.14"\n',
            "app/pyproject.toml": "[tool.ruff]\nline-length = 88\n",
        },
    )

    assert ctx.python_targets.for_path(tmp_path / "app/main.py").minimum == (3, 14)
