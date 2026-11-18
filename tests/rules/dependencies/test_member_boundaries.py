from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.errors import AnalysisError
from smelt.engine.check import CheckOptions
from tests.helpers import MEMBER_BOUNDARIES, check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


def _workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    return write_project(tmp_path, {**MEMBER_BOUNDARIES, **files})


def test_allowed_member_and_same_member_imports_pass(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "packages/core/src/core/consumer.py": "from core.model import Order\nimport requests\n"
        },
    )

    assert violations(root) == []


def test_member_boundary_reports_the_import_and_semantic_edge(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {"packages/core/src/core/model.py": "from api.http import Order\n"},
    )

    [found] = violations(root, CheckOptions(select=("SMT111",)))

    assert (found.code, found.path, found.line, found.column, found.member) == (
        "SMT111",
        "packages/core/src/core/model.py",
        1,
        22,
        "packages/core",
    )
    assert found.target_module == "api.http"
    assert found.edge == "packages/core -> services/api"
    assert found.expected == {
        "source_member": "packages/core",
        "target_member": "services/api",
        "may_depend_on": [],
    }


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("members: {'packages/*': {may_depend_on: []}}", []),
        ("members: {'services/*': {may_depend_on: []}}", ["SMT111"]),
        (
            "members: {'services/*': {may_depend_on: []}, 'services/api': {may_depend_on: ['packages/*']}}",
            [],
        ),
    ],
)
def test_unmatched_members_and_overlapping_globs(
    tmp_path: Path, policy: str, expected: list[str]
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {workspace: uv, source_roots: [src]}\narchitecture:\n  "
            + policy
            + "\n"
        },
    )

    assert [v.code for v in violations(root)] == expected


@pytest.mark.parametrize("mode", ["include", "ignore"])
def test_type_checking_imports_follow_root_policy(tmp_path: Path, mode: str) -> None:
    root = _workspace(
        tmp_path,
        {
            "packages/core/src/core/model.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from api.http import Order\n",
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + f"  imports: {{type_checking: {mode}}}\n",
        },
    )

    assert [v.code for v in violations(root)] == (
        ["SMT111", "SMT112"] if mode == "include" else []
    )


def test_missing_declaration_is_separate_from_allowed_boundary(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "services/api/pyproject.toml": '[project]\nname = "web-api"\ndependencies = []\n[tool.uv.sources]\ncore-lib = {workspace = true}\n'
        },
    )

    [found] = violations(root)

    assert found.code == "SMT112"
    assert found.member == "services/api"
    assert found.hint
    assert "Declare core-lib" in found.hint
    assert found.expected == {
        "source_member": "services/api",
        "target_member": "packages/core",
        "dependency": "core-lib",
        "declared_members": [],
    }


@pytest.mark.parametrize("selection", [(), ("SMT112",)])
def test_forbidden_dependency_repair_does_not_recommend_declaring_it(
    tmp_path: Path, selection: tuple[str, ...]
) -> None:
    root = _workspace(
        tmp_path,
        {"packages/core/src/core/model.py": "from api.http import Order\n"},
    )

    found = violations(root, CheckOptions(select=selection))

    assert [v.code for v in found] == (
        ["SMT112"] if selection else ["SMT111", "SMT112"]
    )
    hint = found[-1].hint
    assert hint
    assert "Remove this import" in hint
    assert "Declare" not in hint


@pytest.mark.parametrize(
    "metadata",
    [
        '[project]\nname = "web-api"\ndependencies = ["Core_Lib[fast]>=1; python_version < \'3.0\'"]\n',
        '[project]\nname = "web-api"\ndependencies = ["core-lib"]\n[tool.uv.sources]\ncore-lib = {path = "../../packages/core"}\n',
        '[project]\nname = "web-api"\n[project.optional-dependencies]\norders = ["core-lib"]\n',
        '[project]\nname = "web-api"\ndynamic = ["dependencies"]\n',
        "",
    ],
)
def test_declarations_use_distribution_names_sources_and_inference_limits(
    tmp_path: Path, metadata: str
) -> None:
    root = _workspace(tmp_path, {"services/api/pyproject.toml": metadata})

    assert violations(root) == []


def test_root_uv_source_paths_are_root_relative(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": MEMBER_BOUNDARIES["pyproject.toml"]
            + '[tool.uv.sources]\ncore-lib = {path = "packages/core"}\n',
            "services/api/pyproject.toml": '[project]\nname = "web-api"\ndependencies = ["core-lib"]\n',
        },
    )

    assert violations(root) == []


def test_member_source_replaces_the_root_source_even_with_a_marker(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": MEMBER_BOUNDARIES["pyproject.toml"]
            + "[tool.uv.sources]\ncore-lib = {workspace = true}\n",
            "services/api/pyproject.toml": '[project]\nname = "web-api"\ndependencies = ["core-lib"]\n[tool.uv.sources]\ncore-lib = [{path = "../../vendor/core", marker = "sys_platform == \'other\'"}]\n',
        },
    )

    [found] = violations(root)

    assert found.code == "SMT112"
    assert found.member == "services/api"


def test_undeclared_import_of_a_namespace_package_is_reported(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "services/api/pyproject.toml": '[project]\nname = "web-api"\n',
            "services/api/src/api/http.py": "import core\n",
        },
    )
    (root / "packages/core/src/core/__init__.py").unlink()

    [found] = violations(root)

    assert found.code == "SMT112"
    assert found.target_module == "core"


def test_indirect_imports_do_not_require_direct_dependency_declarations(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "  imports: {transitive: true}\n",
            "packages/core/pyproject.toml": '[project]\nname = "core-lib"\ndependencies = ["storage"]\n',
            "packages/core/src/core/model.py": "from storage.db import Order\n",
            "packages/storage/pyproject.toml": '[project]\nname = "storage"\n',
            "packages/storage/src/storage/__init__.py": "",
            "packages/storage/src/storage/db.py": "class Order: pass\n",
        },
    )

    assert violations(root, CheckOptions(select=("SMT112",))) == []


def test_metadata_changes_invalidate_warm_analysis(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {})
    assert violations(root) == []

    write_project(
        root, {"services/api/pyproject.toml": '[project]\nname = "web-api"\n'}
    )
    cached = check(root).report.violations
    uncached = check(root, CheckOptions(use_cache=False)).report.violations

    assert [v.code for v in cached] == ["SMT112"]
    assert cached == uncached

    write_project(
        root,
        {
            "services/api/pyproject.toml": MEMBER_BOUNDARIES[
                "services/api/pyproject.toml"
            ]
        },
    )
    assert violations(root) == []


@pytest.mark.parametrize(
    "metadata", ["[project", '[project]\ndependencies = ["!bad"]\n']
)
def test_unreadable_metadata_is_an_analysis_error(
    tmp_path: Path, metadata: str
) -> None:
    root = _workspace(tmp_path, {"services/api/pyproject.toml": metadata})

    with pytest.raises(AnalysisError, match=r"services/api/pyproject\.toml"):
        violations(root)


def test_member_rules_and_suppressions_retain_their_scope(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "packages/core/smelt.yml": "rules: {architecture/member-boundary: {severity: warning, reason: Reviewing the migration boundary.}}\n",
            "packages/core/src/core/model.py": "from api.http import Order  # smelt: ignore[SMT112] -- Host provides the API during migration.\n",
        },
    )

    [found] = violations(root)

    assert found.code == "SMT111"
    assert found.severity.value == "warning"


def test_root_project_participates_as_dot(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "gateway"\n'
            + MEMBER_BOUNDARIES["pyproject.toml"],
            "src/gateway/__init__.py": "from core.model import Order\n",
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "    '.': {may_depend_on: []}\n",
        },
    )

    found = violations(root)

    assert [v.code for v in found] == ["SMT111", "SMT112"]
    assert all(v.member == "." for v in found)
