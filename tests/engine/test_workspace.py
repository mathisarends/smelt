from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.workspace import WorkspaceMember, import_root, workspace_members
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path

NAMESPACE_BACKEND = "[tool.uv.build-backend]\nnamespace = true\n"


def _workspace(root: Path, members: str, exclude: str = "[]") -> None:
    write_project(
        root,
        {
            "pyproject.toml": (
                f"[tool.uv.workspace]\nmembers = {members}\nexclude = {exclude}\n"
            )
        },
    )


class TestWorkspaceMembers:
    def test_every_declared_member_has_packages_or_a_reason(
        self, tmp_path: Path
    ) -> None:
        _workspace(
            tmp_path,
            '["backend", "e2e/stack", "libs/*", "tools/missing"]',
            exclude='["libs/legacy"]',
        )
        write_project(
            tmp_path,
            {
                "backend/src/backend/__init__.py": "",
                "e2e/stack/pyproject.toml": NAMESPACE_BACKEND,
                "e2e/stack/src/e2e_stack/server/__init__.py": "",
                "e2e/stack/src/e2e_stack/fake_llm/app.py": "",
                "libs/legacy/src/legacy/__init__.py": "",
                "libs/docs_only/README.md": "",
            },
        )

        members = workspace_members(tmp_path)

        assert members == [
            WorkspaceMember("backend", "backend/src", ("backend",)),
            WorkspaceMember(
                "e2e/stack", "e2e/stack/src", ("e2e_stack",), ("e2e_stack",)
            ),
            WorkspaceMember(
                "libs/docs_only",
                skipped="no Python package in src/ or the member directory",
            ),
            WorkspaceMember(
                "libs/legacy", skipped="excluded by tool.uv.workspace.exclude"
            ),
            WorkspaceMember("tools/missing", skipped="matches no directory"),
        ]


class TestImportRoot:
    def test_namespace_in_src_needs_a_regular_child_package(
        self, tmp_path: Path
    ) -> None:
        write_project(
            tmp_path,
            {
                "src/space/inner/__init__.py": "",
                "src/loose/nested/script.py": "",
            },
        )

        located = import_root(tmp_path)

        assert located is not None
        base, packages, namespaces = located
        assert base == tmp_path / "src"
        assert [p.name for p in packages] == ["space"]
        assert [p.name for p in namespaces] == ["space"]

    def test_directory_layout_ignores_namespaces_without_build_metadata(
        self, tmp_path: Path
    ) -> None:
        write_project(
            tmp_path,
            {
                "app/__init__.py": "",
                "deploy/hermes/__init__.py": "",
            },
        )

        located = import_root(tmp_path)

        assert located is not None
        assert [p.name for p in located[1]] == ["app"]

    def test_module_name_selects_the_declared_package(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                "pyproject.toml": '[tool.uv.build-backend]\nmodule-name = "stack"\n',
                "stack/server/__init__.py": "",
                "deploy/hermes/__init__.py": "",
            },
        )

        located = import_root(tmp_path)

        assert located is not None
        assert [p.name for p in located[1]] == ["stack"]
