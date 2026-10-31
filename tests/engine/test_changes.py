from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.parsing import AnalysisError
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.render.machine import render_json
from smelt.engine.changes import capture_repository, export_base, resolve_comparison
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, check, codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.mark.parametrize("committed", [False, True])
def test_repository_capture_keeps_the_observed_head_when_head_moves(
    tmp_path: Path,
    committed: bool,
) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    if committed:
        _git(tmp_path, "commit", "--allow-empty", "-qm", "initial")
    nested = tmp_path / "nested"
    nested.mkdir()
    captured = capture_repository(nested)
    assert captured.path == tmp_path.resolve()
    assert (captured.head is not None) == committed
    _git(tmp_path, "commit", "--allow-empty", "-qm", "later")
    _, comparison = resolve_comparison(tmp_path, None, captured=captured)
    assert comparison.head == comparison.revision == captured.head


def test_export_ignore_does_not_hide_existing_findings(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": LAYERED_CONFIG,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/infra/__init__.py": "",
            "app/infra/db.py": "",
            "app/domain/service.py": "from app.infra import db\n",
            ".gitattributes": "app/domain/service.py export-ignore\n",
        },
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    assert not violations(tmp_path, CheckOptions(changed=True))


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

        repository, comparison = resolve_comparison(repo, None)
        snapshot = export_base(repo, repository, comparison, dest)

        assert snapshot == dest
        assert (dest / "app/application/service.py").read_text() == ""
        assert comparison.revision == comparison.head
        assert comparison.to_json()["merge_base"] is None

    def test_without_commits_there_is_no_base(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        _git(tmp_path, "init", "-q")

        repository, comparison = resolve_comparison(tmp_path, None)
        snapshot = export_base(
            tmp_path, repository, comparison, tmp_path_factory.mktemp("base")
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
    @pytest.mark.parametrize(
        ("code", "policy", "decorator"),
        [
            ("SMT316", "rules: {internal-public-member: warning}", ""),
            ("SMT317", "rules: {internal-static-helper: hint}", "    @staticmethod\n"),
        ],
    )
    def test_member_findings_keep_the_owning_class_in_their_identity(
        self, repo: Path, code: str, policy: str, decorator: str
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + f"\nstructure:\n  contracts:\n    members: {{match: app, {policy}}}\n",
            encoding="utf-8",
        )
        source = f"class Original:\n{decorator}    def emit(self): pass\n"
        write_project(repo, {"app/application/service.py": source})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "original member")
        write_project(
            repo,
            {"app/application/service.py": source.replace("Original", "Replacement")},
        )

        [finding] = violations(repo, CheckOptions(changed=True, select=(code,)))

        assert finding.target_module == "app.application.service.Replacement.emit"

    def test_removing_an_external_method_user_reports_the_unchanged_definition(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    members:\n      match: app\n      rules:\n        internal-public-member: warning\n",
            encoding="utf-8",
        )
        write_project(
            repo,
            {
                "app/application/service.py": "class Browser:\n    def emit(self): pass",
                "app/application/consumer.py": "from .service import Browser\nBrowser().emit()",
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "external method user")
        write_project(repo, {"app/application/consumer.py": ""})

        [finding] = violations(repo, CheckOptions(changed=True, select=("SMT316",)))

        assert (finding.path, finding.line) == ("app/application/service.py", 2)
        assert finding.expected is not None
        assert finding.expected["suggested_name"] == "_emit"

    @pytest.mark.parametrize(
        ("expected", "policy", "source", "test"),
        [
            (
                ("SMT429", 1),
                "test-only-public-function",
                "def helper(): pass\ndef run(): helper()",
                "from app.application.service import helper",
            ),
            (
                ("SMT430", 2),
                "test-only-public-attribute",
                "class Worker:\n    def __init__(self): self.pending = []",
                "from app.application.service import Worker\ndef test_state(): assert Worker().pending == []",
            ),
        ],
    )
    def test_new_test_exposure_reports_an_unchanged_production_definition(
        self, repo: Path, expected: tuple[str, int], policy: str, source: str, test: str
    ) -> None:
        code, line = expected
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text() + f"\nrules: {{{policy}: warning}}\n",
            encoding="utf-8",
        )
        write_project(
            repo,
            {
                "app/application/service.py": source,
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "before direct helper tests")
        write_project(
            repo,
            {"tests/test_service.py": test},
        )

        [finding] = violations(repo, CheckOptions(changed=True, select=(code,)))

        assert (finding.code, finding.path, finding.line) == (
            code,
            "app/application/service.py",
            line,
        )

    @pytest.mark.parametrize(
        ("code", "policy", "source", "test"),
        [
            (
                "SMT429",
                "test-only-public-function",
                "def helper(): pass",
                "from app.application.service import helper",
            ),
            (
                "SMT430",
                "test-only-public-attribute",
                "class Worker:\n    def __init__(self): self.pending = []",
                "from app.application.service import Worker\ndef test_state(): assert Worker().pending == []",
            ),
        ],
    )
    def test_extra_test_references_do_not_make_an_existing_visibility_finding_new(
        self, repo: Path, code: str, policy: str, source: str, test: str
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text() + f"\nrules: {{{policy}: warning}}\n",
            encoding="utf-8",
        )
        write_project(
            repo,
            {
                "app/application/service.py": source,
                "tests/test_service.py": test,
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "existing direct helper test")
        write_project(
            repo,
            {"tests/test_other.py": test},
        )

        assert violations(repo, CheckOptions(changed=True, select=(code,))) == []

    @pytest.mark.parametrize("existing_exposure", [False, True])
    def test_changed_attribute_exposure_reports_only_a_new_public_contract(
        self, repo: Path, existing_exposure: bool
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text() + "\nrules: {test-only-public-attribute: warning}\n",
            encoding="utf-8",
        )
        test = "from app.application.service import Worker\ndef test_state(): assert Worker().pending == []"
        files = {
            "app/application/service.py": "class Worker:\n    def __init__(self): self.pending = []",
        }
        if existing_exposure:
            files["tests/test_service.py"] = test
        write_project(repo, files)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "before additional attribute test")
        write_project(repo, {"tests/test_other.py": test})

        findings = violations(repo, CheckOptions(changed=True, select=("SMT430",)))

        if existing_exposure:
            assert findings == []
        else:
            [finding] = findings
            assert (finding.code, finding.path, finding.line) == (
                "SMT430",
                "app/application/service.py",
                2,
            )

    def test_new_ordering_inversions_cannot_hide_in_an_existing_section(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        public-definitions-first: error\n",
            encoding="utf-8",
        )
        write_project(
            repo,
            {"app/application/service.py": "def _existing(): pass\ndef api(): pass\n"},
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "accept existing ordering")
        write_project(
            repo,
            {
                "app/application/service.py": "def _existing(): pass\ndef _new(): pass\ndef api(): pass\n"
            },
        )

        found = violations(repo, CheckOptions(changed=True, select=("SMT313",)))

        assert len(found) == 1
        assert found[0].expected is not None
        assert len(found[0].expected["ordering_edges"]) == 2

    def test_pruning_ordering_debt_does_not_accept_added_inversions(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        public-definitions-first: error\n",
            encoding="utf-8",
        )
        write_project(
            repo,
            {"app/application/service.py": "def _existing(): pass\ndef api(): pass\n"},
        )
        options = CheckOptions(select=("SMT313",))
        baseline = Debt.from_violations(violations(repo, options), lambda _: "")
        write_project(
            repo,
            {
                "app/application/service.py": "def _existing(): pass\ndef _new(): pass\ndef api(): pass\n"
            },
        )
        current = violations(repo, options)

        new, known, _ = baseline.match(current, lambda _: "")
        pruned = Debt.from_violations(known, lambda _: "")

        assert len(new) == 1
        assert len(pruned.entries) == 1
        assert pruned.match(current, lambda _: "")[0]

    def test_ordering_baseline_is_independent_of_whitespace_and_comments(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        public-definitions-first: error\n",
            encoding="utf-8",
        )
        write_project(
            repo,
            {"app/application/service.py": "def _existing(): pass\ndef api(): pass\n"},
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "existing order")
        write_project(
            repo,
            {
                "app/application/service.py": "# comment\ndef _existing():\n    pass\n\n\ndef api():\n    pass\n"
            },
        )

        assert violations(repo, CheckOptions(changed=True, select=("SMT313",))) == []

    def test_new_private_module_names_are_reported_without_repeating_existing_ones(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    names: {match: app, private_modules: forbid}\n",
            encoding="utf-8",
        )
        write_project(repo, {"app/domain/_legacy.py": ""})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "declare module naming convention")
        write_project(repo, {"app/domain/_new.py": ""})

        found = violations(repo, CheckOptions(changed=True, select=("SMT312",)))

        assert [(v.code, v.source_module) for v in found] == [
            ("SMT312", "app.domain._new")
        ]

    def test_import_conventions_are_checked_for_new_changes(self, repo: Path) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    api: {match: app.domain, bundle_imports_at: 2}\n    style: {match: app, imports: {style: absolute}}\n",
            encoding="utf-8",
        )
        write_project(
            repo,
            {
                "app/domain/one.py": "",
                "app/domain/two.py": "",
                "app/application/service.py": "from app.domain import one\n",
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "declare import conventions")
        write_project(
            repo, {"app/application/service.py": "from ..domain import one, two\n"}
        )

        found = violations(
            repo, CheckOptions(changed=True, select=("SMT108", "SMT308"))
        )

        assert {v.code for v in found} == {"SMT108", "SMT308"}

    def test_deleting_a_required_entry_point_is_a_new_finding(self, repo: Path) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    core: {match: app.domain, required: [api]}\n",
            encoding="utf-8",
        )
        write_project(repo, {"app/domain/api.py": ""})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "add package contract")
        (repo / "app/domain/api.py").unlink()

        found = violations(repo, CheckOptions(changed=True, select=("SMT307",)))

        assert [(v.code, v.target_module) for v in found] == [
            ("SMT307", "app.domain.api")
        ]

    def test_new_private_import_is_reported_but_existing_one_is_not(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text()
            + "\nstructure:\n  contracts:\n    data: {match: app.infra, public: ['.']}\n",
            encoding="utf-8",
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "declare public facade")
        write_project(
            repo, {"app/application/service.py": "from app.infra import db\n"}
        )

        found = violations(repo, CheckOptions(changed=True, select=("SMT107",)))

        assert [(v.code, v.source_module) for v in found] == [
            ("SMT107", "app.application.service")
        ]

    def test_existing_coverage_gaps_remain_visible_in_changed_mode(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "root_packages: [app]", "root_packages: [app]\n  coverage: strict"
            ),
            encoding="utf-8",
        )
        write_project(repo, {"client/api.py": "VALUE = 1\n"})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "Existing coverage gap")

        [found] = violations(repo, CheckOptions(changed=True))

        assert (found.code, found.path) == ("SMT306", "client/api.py")

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
            timeout=10,
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
