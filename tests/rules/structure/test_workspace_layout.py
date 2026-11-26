from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.helpers import violations, write_policy_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("layout", "package_path", "expected"),
    [
        ("flat", "backend", None),
        ("src", "src/backend", None),
        ("flat", "src/backend", "backend"),
        ("src", "backend", "src/backend"),
        ("src", "src/server", "src/backend"),
    ],
)
def test_workspace_policy_checks_source_location_and_package_name(
    tmp_path: Path, layout: str, package_path: str, *, expected: str | None
) -> None:
    member = "services/backend"
    path = f"{member}/{package_path}/__init__.py"
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": (
                f"version: 2\nproject: {{workspace: uv, workspace_layout: {layout}}}\n"
                "rules: {layout/workspace-layout-convention: error}\n"
            ),
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*"]\n',
            f"{member}/pyproject.toml": '[project]\nname = "web-backend"\n',
            path: "",
            f"{member}/{package_path}/internal/__init__.py": "",
        },
    )

    found = violations(root)

    if expected is None:
        assert found == []
        return
    [finding] = found
    assert (finding.code, finding.path, finding.member) == ("LAY013", path, member)
    assert finding.expected == {"path": f"{member}/{expected}", "layout": layout}


def test_workspace_policy_uses_one_layout_across_members(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": (
                "version: 2\nproject: {workspace: uv, workspace_layout: src}\n"
                "rules: {layout/workspace-layout-convention: error}\n"
            ),
            "pyproject.toml": (
                '[project]\nname = "workspace-app"\n[tool.uv.workspace]\n'
                'members = ["packages/*"]\nexclude = ["packages/excluded"]\n'
            ),
            "src/workspace_app/__init__.py": "",
            "packages/core-lib/pyproject.toml": '[project]\nname = "core-lib"\n',
            "packages/core-lib/src/core_lib/__init__.py": "",
            "packages/core-lib/tests/__init__.py": "",
            "packages/backend/pyproject.toml": '[project]\nname = "backend"\n',
            "packages/backend/backend/__init__.py": "",
            "packages/excluded/pyproject.toml": '[project]\nname = "excluded"\n',
            "packages/excluded/excluded/__init__.py": "",
        },
    )

    assert [finding.path for finding in violations(root)] == [
        "packages/backend/backend/__init__.py"
    ]


def test_workspace_policy_checks_namespace_at_custom_source_root(
    tmp_path: Path,
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": (
                "version: 2\n"
                "project: {workspace: uv, workspace_layout: src, "
                "source_roots: [lib], root_packages: [backend]}\n"
                "rules: {layout/workspace-layout-convention: error}\n"
            ),
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["backend"]\n',
            "backend/pyproject.toml": '[project]\nname = "backend"\n',
            "backend/lib/backend/models.py": "",
        },
    )

    [finding] = violations(root)

    assert (finding.path, finding.expected) == (
        "backend/lib/backend",
        {"path": "backend/src/backend", "layout": "src"},
    )
