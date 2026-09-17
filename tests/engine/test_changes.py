import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from smelt.engine.changes import git_changes
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


class TestGitChanges:
    def test_clean_tree_has_no_changes(self, repo: Path) -> None:
        assert git_changes(repo).paths == frozenset()

    def test_counts_modified_and_untracked_lines(self, repo: Path) -> None:
        (repo / "app/application/service.py").write_text("a = 1\nb = 2\n")
        (repo / "app/application/new.py").write_text("c = 3\n")

        changes = git_changes(repo)

        assert {path: (c.added, c.deleted) for path, c in changes.files.items()} == {
            "app/application/new.py": (1, 0),
            "app/application/service.py": (2, 0),
        }

    def test_base_compares_against_merge_base(self, repo: Path) -> None:
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "app/application/service.py").write_text("x = 1\n")
        _git(repo, "commit", "-q", "-am", "change")

        assert git_changes(repo, "main").paths == {"app/application/service.py"}


class TestChangedMode:
    def test_reports_only_violations_in_changed_files(self, repo: Path) -> None:
        (repo / "app/application/service.py").write_text("from app.infra import db\n")

        found = violations(repo, CheckOptions(changed=True))

        assert codes_at(found) == [("SMT101", "app/application/service.py", 1)]

    def test_full_check_still_sees_old_violations(self, repo: Path) -> None:
        (repo / "app/application/service.py").write_text("from app.infra import db\n")

        assert len(violations(repo)) == 2

    def test_violation_touching_a_changed_target_is_reported(self, repo: Path) -> None:
        (repo / "app/infra/db.py").write_text("x = 1\n")

        found = violations(repo, CheckOptions(changed=True))

        assert codes_at(found) == [("SMT101", "app/application/old.py", 1)]
