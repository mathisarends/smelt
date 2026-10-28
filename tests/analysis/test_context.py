from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.context import AnalysisContext
from smelt.analysis.parsing import AnalysisError
from smelt.config import load_config
from tests.helpers import LAYERED_CONFIG, write_project

if TYPE_CHECKING:
    from pathlib import Path


class TestAnalysisContext:
    def test_split_namespace_is_fully_indexed_and_analyzed(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": LAYERED_CONFIG.replace(
                    "root_packages: [app]",
                    "root_packages: [app]\n  source_roots: [one/src, two/src]",
                ),
                "one/src/app/domain/service.py": "from app.infra.db import Db\n",
                "two/src/app/infra/db.py": "class Db: pass\n",
            },
        )
        ctx = AnalysisContext(root, load_config(root / "smelt.yaml").config)

        assert set(ctx.files.sources) == {"app.domain.service", "app.infra.db"}
        assert ctx.imports.graph.direct_import_exists(
            importer="app.domain.service", imported="app.infra.db"
        )
        assert ctx.files.module_to_path("app.infra.new") == "two/src/app/infra/new.py"

    @pytest.mark.parametrize("regular", [False, True])
    def test_ambiguous_packages_and_modules_name_both_paths(
        self, tmp_path: Path, regular: bool
    ) -> None:
        files = {
            "smelt.yaml": LAYERED_CONFIG.replace(
                "root_packages: [app]",
                "root_packages: [app]\n  source_roots: [one/src, two/src]",
            ),
            "one/src/app/model.py": "VALUE = 1\n",
            "two/src/app/model.py": "VALUE = 2\n",
        }
        if regular:
            files.update({"one/src/app/__init__.py": "", "two/src/app/__init__.py": ""})
        root = write_project(tmp_path, files)
        ctx = AnalysisContext(root, load_config(root / "smelt.yaml").config)

        with pytest.raises(AnalysisError, match="ambiguous") as caught:
            _ = ctx.files

        assert "one/src/app" in str(caught.value)
        assert "two/src/app" in str(caught.value)

    def test_regular_package_wins_over_namespace_scaffolding(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": LAYERED_CONFIG.replace(
                    "root_packages: [app]",
                    "root_packages: [app]\n  source_roots: [., app/src]",
                ),
                "app/tool.py": "",
                "app/src/app/__init__.py": "",
                "app/src/app/domain/model.py": "",
            },
        )
        ctx = AnalysisContext(root, load_config(root / "smelt.yaml").config)

        assert set(ctx.files.sources) == {"app", "app.domain.model"}
        assert set(ctx.imports.modules()) == set(ctx.files.sources)

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
