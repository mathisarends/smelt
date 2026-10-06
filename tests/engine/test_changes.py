from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from smelt.engine.changes import base_snapshot
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_project(
        tmp_path,
        {
            "smelt.yaml": LAYERED_CONFIG,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/infra/__init__.py": "",
            "app/infra/db.py": "",
            "app/application/__init__.py": "",
            "app/application/old.py": "from app.infra import db\n",
            "app/application/service.py": "",
        },
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


class TestBaseSnapshot:
    def test_exports_head(
        self, repo: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        (repo / "app/application/service.py").write_text("x = 1\n")
        dest = tmp_path_factory.mktemp("base")

        snapshot = base_snapshot(repo, None, dest)

        assert snapshot == dest
        assert (dest / "app/application/service.py").read_text() == ""

    def test_without_commits_there_is_no_base(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        _git(tmp_path, "init", "-q")

        assert base_snapshot(tmp_path, None, tmp_path_factory.mktemp("base")) is None


class TestChangedMode:
    @pytest.mark.parametrize(
        "source",
        [
            "from app.infra import db  # explanation only\n",
            "from app.infra import (\n    db,\n)\n",
            "from app.infra import db as database\n",
        ],
    )
    def test_import_spelling_does_not_make_existing_violation_new(
        self, repo: Path, source: str
    ) -> None:
        (repo / "app/application/old.py").write_text(source, encoding="utf-8")

        assert violations(repo, CheckOptions(changed=True)) == []

    def test_additional_identical_import_still_is_new(self, repo: Path) -> None:
        (repo / "app/application/old.py").write_text(
            "from app.infra import db  # explanation only\nfrom app.infra import db\n",
            encoding="utf-8",
        )

        assert len(violations(repo, CheckOptions(changed=True))) == 1

    def test_reports_only_introduced_violations(self, repo: Path) -> None:
        (repo / "app/application/service.py").write_text("from app.infra import db\n")

        found = violations(repo, CheckOptions(changed=True))

        assert codes_at(found) == [("SMT101", "app/application/service.py", 1)]

    def test_full_check_still_sees_old_violations(self, repo: Path) -> None:
        (repo / "app/application/service.py").write_text("from app.infra import db\n")

        assert len(violations(repo)) == 2

    def test_old_violation_in_an_edited_file_is_not_reported(self, repo: Path) -> None:
        (repo / "app/application/old.py").write_text(
            "import os\n\nfrom app.infra import db\n"
        )

        assert violations(repo, CheckOptions(changed=True)) == []

    def test_editing_an_import_target_reports_nothing_old(self, repo: Path) -> None:
        (repo / "app/infra/db.py").write_text("x = 1\n")

        assert violations(repo, CheckOptions(changed=True)) == []

    def test_untracked_file_is_new(self, repo: Path) -> None:
        (repo / "app/application/new.py").write_text("from app.infra import db\n")

        found = violations(repo, CheckOptions(changed=True))

        assert codes_at(found) == [("SMT101", "app/application/new.py", 1)]

    def test_base_compares_against_merge_base(self, repo: Path) -> None:
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "app/application/service.py").write_text("from app.infra import db\n")
        _git(repo, "commit", "-q", "-am", "change")

        found = violations(repo, CheckOptions(changed=True, base="main"))

        assert codes_at(found) == [("SMT101", "app/application/service.py", 1)]
