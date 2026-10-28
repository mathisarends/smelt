from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.parsing import AnalysisError
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.render.machine import render_json
from smelt.engine.changes import base_snapshot
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, check, codes_at, violations, write_project

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

        snapshot, comparison = base_snapshot(repo, None, dest)

        assert snapshot == dest
        assert (dest / "app/application/service.py").read_text() == ""
        assert comparison.revision == comparison.head
        assert comparison.to_json()["merge_base"] is None

    def test_without_commits_there_is_no_base(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        _git(tmp_path, "init", "-q")

        snapshot, comparison = base_snapshot(
            tmp_path, None, tmp_path_factory.mktemp("base")
        )

        assert snapshot is None
        assert comparison.to_json() == {
            "mode": "changed",
            "base": None,
            "merge_base": None,
            "compared_revision": None,
            "head": None,
            "working_tree": True,
        }


class TestChangedMode:
    def test_first_python_package_after_a_committed_readme(
        self, tmp_path: Path
    ) -> None:
        _git(tmp_path, "init", "-q")
        write_project(tmp_path, {"README.md": "Project\n"})
        _git(tmp_path, "add", "README.md")
        _git(tmp_path, "commit", "-qm", "Readme")
        write_project(
            tmp_path,
            {
                "smelt.yaml": LAYERED_CONFIG,
                "app/__init__.py": "",
                "app/domain/model.py": "from app.infra import db\n",
                "app/infra/db.py": "",
            },
        )

        found = violations(tmp_path, CheckOptions(changed=True))

        assert codes_at(found) == [("SMT101", "app/domain/model.py", 1)]

    def test_invalid_historical_source_is_still_an_error(self, repo: Path) -> None:
        path = repo / "app/domain/model.py"
        path.write_text("def invalid(:\n", encoding="utf-8")
        _git(repo, "add", "app/domain/model.py")
        _git(repo, "commit", "-qm", "Invalid historical source")
        path.write_text("VALUE = 1\n", encoding="utf-8")

        with pytest.raises(AnalysisError, match="syntax error"):
            check(repo, CheckOptions(changed=True))

    def test_replacing_an_indirect_forbidden_target_is_new(self, repo: Path) -> None:
        write_project(
            repo,
            {
                "smelt.yaml": LAYERED_CONFIG
                + "    presentation: {path: presentation}\n  imports: {transitive: true}\n",
                "app/__init__.py": "from app.infra.db import Db\n",
                "app/infra/db.py": "class Db: pass\n",
                "app/presentation/api.py": "class Api: pass\n",
                "app/domain/model.py": "from app import Db\n",
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "Existing indirect violation")
        write_project(
            repo,
            {
                "app/__init__.py": "from app.presentation.api import Api\n",
                "app/domain/model.py": "from app import Api\n",
            },
        )

        [found] = violations(repo, CheckOptions(changed=True, select=("SMT101",)))

        assert found.target_module == "app.presentation.api"
        assert found.edge == "domain -> presentation"

    @pytest.mark.parametrize(
        ("before", "after", "introduced"),
        [
            ({"a": "b", "b": "a", "c": ""}, {"a": "b", "b": "a,c", "c": "b"}, True),
            ({"a": "b", "b": "c", "c": "a"}, {"a": "b,c", "b": "c", "c": "a"}, True),
            ({"a": "b", "b": "a,c", "c": "b"}, {"a": "b", "b": "a", "c": "b"}, False),
            (
                {"a": "b", "b": "a,c", "c": "b,d", "d": "c"},
                {"a": "b", "b": "a", "c": "d", "d": "c"},
                False,
            ),
        ],
    )
    def test_cycle_changes_compare_all_cyclic_edges(
        self,
        repo: Path,
        before: dict[str, str],
        after: dict[str, str],
        introduced: bool,
    ) -> None:
        def write_edges(edges: dict[str, str]) -> None:
            write_project(
                repo,
                {
                    f"app/domain/{name}.py": f"from app.domain import {targets}\n"
                    if targets
                    else "VALUE = 1\n"
                    for name, targets in edges.items()
                },
            )

        write_edges(before)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "Existing cyclic group")
        write_edges(after)

        found = violations(repo, CheckOptions(changed=True, select=("SMT104",)))

        assert bool(found) is introduced

    def test_pruning_a_growing_cycle_preserves_only_known_edges(
        self, repo: Path
    ) -> None:
        write_project(
            repo,
            {
                "app/domain/a.py": "from app.domain import b\n",
                "app/domain/b.py": "from app.domain import a\n",
                "app/domain/c.py": "VALUE = 1\n",
            },
        )
        options = CheckOptions(select=("SMT104",))
        debt = Debt.from_violations(violations(repo, options), lambda v: "")
        write_project(
            repo,
            {
                "app/domain/b.py": "from app.domain import a, c\n",
                "app/domain/c.py": "from app.domain import b\n",
            },
        )
        current = violations(repo, options)

        new, known, resolved = debt.match(current, lambda v: "")
        pruned = Debt.from_violations(known, lambda v: "")

        assert len(new) == 1
        assert resolved == []
        assert {entry.fingerprint for entry in pruned.entries} == {
            entry.fingerprint for entry in debt.entries
        }
        assert len(pruned.match(current, lambda v: "")[0]) == 1

    def test_report_names_the_base_and_its_merge_base(self, repo: Path) -> None:
        _git(repo, "checkout", "-q", "-b", "topic")
        (repo / "app/application/service.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "commit", "-q", "-am", "topic work")

        report = check(repo, CheckOptions(changed=True, base="main")).report
        document = json.loads(render_json(report))

        main = subprocess.run(
            ["git", "rev-parse", "main"],  # noqa: S607
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert document["scope"]["mode"] == "changed"
        comparison = document["comparison"]
        assert (comparison["base"], comparison["merge_base"]) == ("main", main)
        assert comparison["compared_revision"] == main
        assert comparison["head"] != main

    def test_full_run_has_no_comparison(self, repo: Path) -> None:
        document = json.loads(render_json(check(repo).report))

        assert document["scope"]["mode"] == "full"
        assert document["comparison"] is None

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
