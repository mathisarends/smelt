from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext
from smelt.config import load_config
from tests.helpers import LAYERED_CONFIG, write_project

if TYPE_CHECKING:
    from pathlib import Path


class TestAnalysisContext:
    def test_unresolved_root_still_finds_every_module(self, tmp_path: Path) -> None:
        # macOS temp dirs sit behind a symlink and Windows ones may use 8.3 names;
        # a ".." segment gives the same unresolved-path mismatch on every platform.
        project = write_project(
            tmp_path / "project",
            {
                "smelt.yaml": LAYERED_CONFIG,
                "app/__init__.py": "",
                "app/domain/__init__.py": "",
                "app/application/__init__.py": "",
                "app/application/service.py": "from app import domain\n",
            },
        )
        (tmp_path / "elsewhere").mkdir()
        config = load_config(project / "smelt.yaml").config

        ctx = AnalysisContext(tmp_path / "elsewhere" / ".." / "project", config)

        assert "app.application.service" in ctx.imports.graph.modules
