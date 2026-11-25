from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.errors import AnalysisError
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.render.machine import render_json
from smelt.engine.changes import capture_repository, resolve_comparison
from smelt.engine.check import CheckOptions
from tests.helpers import (
    LAYERED_CONFIG,
    add_rules,
    check,
    codes_at,
    violations,
    write_policy_project,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.mark.parametrize(
    ("code", "count"), [("ARC007", 2), ("IMP001", 1), ("IMP004", 1)]
)
def test_changed_checks_find_new_deep_test_imports(
    tmp_path: Path, code: str, count: int
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\narchitecture:\n  packages:\n    api: {match: app.api, public: ['.'], min_imports_to_bundle: 2}\n",
            "app/__init__.py": "",
            "app/api/__init__.py": "from .impl import Item\n",
            "app/api/impl.py": "class Item: pass\n",
            "app/api/other.py": "class Other: pass\n",
            "tests/test_api.py": "from app.api import Item\n",
        },
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "public API baseline")
    assert violations(root, CheckOptions(changed=True, select=(code,))) == []
    write_policy_project(
        root,
        {
            "tests/test_api.py": "from app.api.impl import Item\nfrom app.api.other import Other\n"
        },
    )
    found = violations(root, CheckOptions(changed=True, select=(code,)))
    assert [(v.code, v.path) for v in found] == [(code, "tests/test_api.py")] * count


def test_changed_literal_group_detects_new_sites_after_a_group_is_accepted(
    tmp_path: Path,
) -> None:
    source = "from typing import Literal\ndef first(x: Literal[1, 2]): pass\n"
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/first.py": source,
            "app/second.py": source.replace("def first", "def second"),
        },
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "accept duplicate group")
    options = CheckOptions(changed=True, select=("TYP005",))
    assert not violations(tmp_path, options)
    write_policy_project(
        tmp_path, {"app/third.py": source.replace("def first", "def third")}
    )
    found = violations(tmp_path, options)
    assert [v.path for v in found] == ["app/third.py"]
    (tmp_path / "app/first.py").write_text(
        source + source.split("\n", maxsplit=1)[1].replace("def first", "def added"),
        encoding="utf-8",
    )
    assert [v.path for v in violations(tmp_path, options)] == [
        "app/first.py",
        "app/third.py",
    ]
    (tmp_path / "app/third.py").unlink()
    (tmp_path / "app/first.py").write_text(source, encoding="utf-8")
    assert not violations(tmp_path, options)


def test_changed_module_fanout_detects_replaced_dependency_at_the_same_count(
    tmp_path: Path,
) -> None:
    source = "from app.first import First\nfrom app.second import Second\n"
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {imports/excess-module-imports: {severity: hint, max: 1}}\n",
            "app/__init__.py": "",
            "app/client.py": source,
            "app/first.py": "First = 1\n",
            "app/second.py": "Second = 1\n",
            "app/third.py": "Third = 1\n",
        },
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "accept existing imports")
    options = CheckOptions(changed=True, select=("IMP002",))
    assert violations(root, options) == []
    write_policy_project(
        root,
        {"app/client.py": source.replace("second import Second", "third import Third")},
    )
    [finding] = violations(root, options)
    assert (finding.code, finding.path) == ("IMP002", "app/client.py")
    assert finding.expected is not None
    assert finding.expected["modules"] == ["app.first", "app.third"]
    assert violations(root, CheckOptions(changed=True, select=("IMP002",))) == [finding]


@pytest.mark.parametrize("change", ["append", "replace"])
def test_changed_private_definitions_detect_new_names(
    tmp_path: Path, change: str
) -> None:
    source = "class _First: pass\nclass _Second: pass\n"
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {visibility/excess-private-definitions: {severity: hint, max: 0}}\n",
            "app/__init__.py": "",
            "app/client.py": source,
        },
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "accept existing private definitions")
    options = CheckOptions(changed=True, select=("VIS007",))
    assert violations(root, options) == []
    # Moving declarations and adding constants do not add counted definitions.
    write_policy_project(root, {"app/client.py": "_LIMIT = 10\n" + "\n" + source})
    assert violations(root, options) == []
    updated = (
        source + "class _Added: pass\n"
        if change == "append"
        else source.replace("_Second", "_Added")
    )
    write_policy_project(root, {"app/client.py": updated})
    [finding] = violations(root, options)
    assert (finding.code, finding.path) == ("VIS007", "app/client.py")
    assert finding.expected is not None
    assert "class:_Added" in finding.expected["definition_edges"]
    assert violations(root, CheckOptions(changed=True, select=("VIS007",))) == [finding]
    write_policy_project(root, {"app/client.py": "class _First: pass\n"})
    assert violations(root, options) == []


def test_changed_multiline_comprehension_detects_new_logic_below_its_anchor(
    tmp_path: Path,
) -> None:
    source = "value = [\n    x for group in groups for x in group\n]\n"
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/client.py": source,
        },
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "accept existing comprehension")
    options = CheckOptions(changed=True, select=("CPX006",))
    assert violations(root, options) == []
    write_policy_project(
        root,
        {
            "app/client.py": source.replace(
                "x for group", "convert(x) if ready else x for group"
            )
        },
    )
    [finding] = violations(root, options)
    assert (finding.code, finding.line) == ("CPX006", 1)
    assert finding.expected is not None
    assert "nontrivial conditional element" in finding.expected["conditions"]
    assert violations(root, CheckOptions(changed=True, select=("CPX006",))) == [finding]


def test_changed_constructor_detects_an_added_decision_at_the_same_method(
    tmp_path: Path,
) -> None:
    source = "class Client:\n    def __init__(self, values):\n        self.values = [x for x in values]\n"
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {complexity/excess-constructor-decisions: {severity: hint, max: 0}}\n",
            "app/__init__.py": "",
            "app/client.py": source,
        },
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "accept existing constructor decision")
    options = CheckOptions(changed=True, select=("CPX007",))
    assert violations(root, options) == []
    write_policy_project(
        root, {"app/client.py": source + "        self.other = [x for x in values]\n"}
    )
    [finding] = violations(root, options)
    assert (finding.code, finding.line) == ("CPX007", 2)
    assert finding.expected is not None
    assert finding.expected["count"] == 2
    assert violations(root, CheckOptions(changed=True, select=("CPX007",))) == [finding]


def test_changed_private_access_detects_an_owner_change_in_a_facade(
    tmp_path: Path,
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "from .first import Store as PublicStore\n",
            "app/first.py": "class Store:\n    _data = None\n",
            "app/second.py": "class Store:\n    _data = None\n",
            "app/client.py": "from app import PublicStore\ndef read(store: PublicStore):\n    return store._data\n",
        },
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "accept existing ownership")
    options = CheckOptions(changed=True, select=("VIS005",))
    assert violations(root, options) == []
    (root / "app/__init__.py").write_text(
        "from .second import Store as PublicStore\n", encoding="utf-8"
    )
    [finding] = violations(root, options)
    assert finding.target_module == "app.second.Store._data"
    assert finding.path == "app/client.py"
    assert violations(root, CheckOptions(changed=True, select=("VIS005",))) == [finding]


def test_changed_filename_policy_reports_only_new_paths(tmp_path: Path) -> None:
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nstyle: {file_naming: {yaml_extension: yml, compose_basename: compose}}\n",
            "app/__init__.py": "",
            "deploy/docker-compose.yaml": "",
        },
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    options = CheckOptions(changed=True, select=("LAY007",))
    assert not violations(tmp_path, options)
    write_policy_project(tmp_path, {"deploy/docker-compose.prod.yaml": ""})
    found = violations(tmp_path, options)
    assert [(v.code, v.path) for v in found] == [
        ("LAY007", "deploy/docker-compose.prod.yaml"),
    ]
    (tmp_path / "deploy/docker-compose.prod.yaml").rename(
        tmp_path / "deploy/compose.prod.yml"
    )
    assert not violations(tmp_path, options)


def test_changed_facade_gap_tracks_new_callers_at_an_unchanged_definition(
    tmp_path: Path,
) -> None:
    caller = "from app.api.impl import Item\n"
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\narchitecture:\n  packages:\n    api: {match: app.api, min_importers_to_export: 2}\n",
            "app/__init__.py": "",
            "app/api/__init__.py": "",
            "app/api/impl.py": "class Item: pass\n",
            "app/first.py": caller,
            "app/second.py": "",
        },
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    options = CheckOptions(select=("ARC008",), changed=True)
    assert violations(tmp_path, options) == []
    write_policy_project(tmp_path, {"app/second.py": caller})
    [finding] = violations(tmp_path, options)
    assert finding.path == "app/api/impl.py"
    assert finding.expected is not None
    assert finding.expected["count"] == 2
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "accept existing gap")
    assert violations(tmp_path, options) == []
    write_policy_project(tmp_path, {"app/third.py": caller})
    [expanded] = violations(tmp_path, options)
    assert expanded.expected is not None
    assert expanded.expected["count"] == 3


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
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": LAYERED_CONFIG,
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


def test_changed_optional_defaults_distinguish_a_replaced_function(repo: Path) -> None:
    original = "def upload(value: int | None = None):\n    return value or 7\n"
    write_policy_project(repo, {"app/application/service.py": original})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "existing optional default")
    options = CheckOptions(changed=True, select=("TYP003",))
    write_policy_project(repo, {"app/application/service.py": "\n\n" + original})
    assert violations(repo, options) == []
    write_policy_project(
        repo,
        {
            "app/application/service.py": "def upload(value: int | None = None):\n    return value if value is not None else 7\n"
            "def download(value: int | None = None):\n    return value or 7\n"
        },
    )
    [finding] = violations(repo, options)
    assert finding.expected is not None
    assert finding.expected["scope"] == "download"


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        timeout=10,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": LAYERED_CONFIG,
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


class TestBaseComparison:
    def test_base_is_head_without_the_working_tree_edit(self, repo: Path) -> None:
        (repo / "app/application/service.py").write_text("x = 1\n")

        repository, comparison = resolve_comparison(repo, None)

        assert comparison.revision is not None
        base = _git(
            repository, "show", f"{comparison.revision}:app/application/service.py"
        )
        assert base == b""
        assert comparison.revision == comparison.head
        assert comparison.to_json()["merge_base"] is None

    def test_changed_check_does_not_materialize_the_base(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unexpected(*args: object, **kwargs: object) -> None:
            pytest.fail("the base revision must be read from Git objects")

        monkeypatch.setattr("tempfile.TemporaryDirectory", unexpected)
        monkeypatch.setattr("tempfile.mkdtemp", unexpected)
        (repo / "app/application/service.py").write_text("x = 1\n")

        assert violations(repo, CheckOptions(changed=True)) == []

    def test_without_commits_there_is_no_base(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q")

        _, comparison = resolve_comparison(tmp_path, None)

        assert comparison.revision is None
        assert comparison.to_json() == {
            "mode": "changed",
            "base": None,
            "merge_base": None,
            "compared_revision": None,
            "head": None,
            "working_tree": True,
        }


class TestChangedMode:
    def test_new_navigation_call_is_not_hidden_by_an_identical_existing_call(
        self, repo: Path
    ) -> None:
        source = "class Controller:\n    def run(self):\n        self.browser.input.motor()\n"
        add_rules(repo, {"complexity/excess-attribute-depth": "hint"})
        write_policy_project(repo, {"app/application/service.py": source})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "existing navigation")
        options = CheckOptions(changed=True, select=("CPX001",))
        assert violations(repo, options) == []
        write_policy_project(
            repo,
            {
                "app/application/service.py": source
                + "        self.browser.input.motor()\n"
            },
        )

        [finding] = violations(repo, options)

        assert finding.code == "CPX001"
        assert finding.line == 4

    @pytest.mark.parametrize(
        ("code", "policy", "decorator"),
        [
            ("VIS001", "rules: {visibility/internal-public-member: warning}", ""),
            (
                "LAY005",
                "rules: {layout/misplaced-static-helper: hint}",
                "    @staticmethod\n",
            ),
        ],
    )
    def test_member_findings_keep_the_owning_class_in_their_identity(
        self, repo: Path, code: str, policy: str, decorator: str
    ) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + f"\nstyle:\n  overrides:\n    members: {{match: app, {policy}}}\n",
            encoding="utf-8",
        )
        source = f"class Original:\n{decorator}    def emit(self): pass\n"
        write_policy_project(repo, {"app/application/service.py": source})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "original member")
        write_policy_project(
            repo,
            {"app/application/service.py": source.replace("Original", "Replacement")},
        )

        [finding] = violations(repo, CheckOptions(changed=True, select=(code,)))

        assert finding.target_module == "app.application.service.Replacement.emit"

    def test_removing_an_external_method_user_reports_the_unchanged_definition(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\nstyle:\n  overrides:\n    members:\n      match: app\n      rules:\n        visibility/internal-public-member: warning\n",
            encoding="utf-8",
        )
        write_policy_project(
            repo,
            {
                "app/application/service.py": "class Browser:\n    def emit(self): pass",
                "app/application/consumer.py": "from .service import Browser\nBrowser().emit()",
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "external method user")
        write_policy_project(repo, {"app/application/consumer.py": ""})

        [finding] = violations(repo, CheckOptions(changed=True, select=("VIS001",)))

        assert (finding.path, finding.line) == ("app/application/service.py", 2)
        assert finding.expected is not None
        assert finding.expected["suggested_name"] == "_emit"

    @pytest.mark.parametrize(
        ("expected", "policy", "source", "test"),
        [
            (
                ("VIS009", 1),
                "visibility/test-only-public-function",
                "def helper(): pass\ndef run(): helper()",
                "from app.application.service import helper",
            ),
            (
                ("VIS010", 2),
                "visibility/test-only-public-attribute",
                "class Worker:\n    def __init__(self): self.pending = []",
                "from app.application.service import Worker\ndef test_state(): assert Worker().pending == []",
            ),
        ],
    )
    def test_new_test_exposure_reports_an_unchanged_production_definition(
        self, repo: Path, expected: tuple[str, int], policy: str, source: str, test: str
    ) -> None:
        code, line = expected
        add_rules(repo, {policy: "warning"})
        write_policy_project(
            repo,
            {
                "app/application/service.py": source,
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "before direct helper tests")
        write_policy_project(
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
                "VIS009",
                "visibility/test-only-public-function",
                "def helper(): pass",
                "from app.application.service import helper",
            ),
            (
                "VIS010",
                "visibility/test-only-public-attribute",
                "class Worker:\n    def __init__(self): self.pending = []",
                "from app.application.service import Worker\ndef test_state(): assert Worker().pending == []",
            ),
        ],
    )
    def test_extra_test_references_do_not_make_an_existing_visibility_finding_new(
        self, repo: Path, code: str, policy: str, source: str, test: str
    ) -> None:
        add_rules(repo, {policy: "warning"})
        write_policy_project(
            repo,
            {
                "app/application/service.py": source,
                "tests/test_service.py": test,
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "existing direct helper test")
        write_policy_project(
            repo,
            {"tests/test_other.py": test},
        )

        assert violations(repo, CheckOptions(changed=True, select=(code,))) == []

    @pytest.mark.parametrize("existing_exposure", [False, True])
    def test_changed_attribute_exposure_reports_only_a_new_public_contract(
        self, repo: Path, existing_exposure: bool
    ) -> None:
        add_rules(repo, {"visibility/test-only-public-attribute": "warning"})
        test = "from app.application.service import Worker\ndef test_state(): assert Worker().pending == []"
        files = {
            "app/application/service.py": "class Worker:\n    def __init__(self): self.pending = []",
        }
        if existing_exposure:
            files["tests/test_service.py"] = test
        write_policy_project(repo, files)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "before additional attribute test")
        write_policy_project(repo, {"tests/test_other.py": test})

        findings = violations(repo, CheckOptions(changed=True, select=("VIS010",)))

        if existing_exposure:
            assert findings == []
        else:
            [finding] = findings
            assert (finding.code, finding.path, finding.line) == (
                "VIS010",
                "app/application/service.py",
                2,
            )

    def test_new_ordering_inversions_cannot_hide_in_an_existing_section(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\nstyle:\n  overrides:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        layout/definition-order: error\n",
            encoding="utf-8",
        )
        write_policy_project(
            repo,
            {"app/application/service.py": "def _existing(): pass\ndef api(): pass\n"},
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "accept existing ordering")
        write_policy_project(
            repo,
            {
                "app/application/service.py": "def _existing(): pass\ndef _new(): pass\ndef api(): pass\n"
            },
        )

        found = violations(repo, CheckOptions(changed=True, select=("LAY004",)))

        assert len(found) == 1
        assert found[0].expected is not None
        assert len(found[0].expected["ordering_edges"]) == 2

    def test_pruning_ordering_debt_does_not_accept_added_inversions(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\nstyle:\n  overrides:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        layout/definition-order: error\n",
            encoding="utf-8",
        )
        write_policy_project(
            repo,
            {"app/application/service.py": "def _existing(): pass\ndef api(): pass\n"},
        )
        options = CheckOptions(select=("LAY004",))
        baseline = Debt.from_violations(violations(repo, options), lambda _: "")
        write_policy_project(
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
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\nstyle:\n  overrides:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        layout/definition-order: error\n",
            encoding="utf-8",
        )
        write_policy_project(
            repo,
            {"app/application/service.py": "def _existing(): pass\ndef api(): pass\n"},
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "existing order")
        write_policy_project(
            repo,
            {
                "app/application/service.py": "# comment\ndef _existing():\n    pass\n\n\ndef api():\n    pass\n"
            },
        )

        assert violations(repo, CheckOptions(changed=True, select=("LAY004",))) == []

    def test_new_private_module_names_are_reported_without_repeating_existing_ones(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\nstyle:\n  overrides:\n    names: {match: app, private_modules: forbid}\n",
            encoding="utf-8",
        )
        write_policy_project(repo, {"app/domain/_legacy.py": ""})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "declare module naming convention")
        write_policy_project(repo, {"app/domain/_new.py": ""})

        found = violations(repo, CheckOptions(changed=True, select=("LAY003",)))

        assert [(v.code, v.source_module) for v in found] == [
            ("LAY003", "app.domain._new")
        ]

    def test_import_conventions_are_checked_for_new_changes(self, repo: Path) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\n  packages:\n    api: {match: app.domain, min_imports_to_bundle: 2}\nstyle:\n  overrides:\n    style: {match: app, imports: {style: absolute}}\n",
            encoding="utf-8",
        )
        write_policy_project(
            repo,
            {
                "app/domain/one.py": "",
                "app/domain/two.py": "",
                "app/application/service.py": "from app.domain import one\n",
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "declare import conventions")
        write_policy_project(
            repo, {"app/application/service.py": "from ..domain import one, two\n"}
        )

        found = violations(
            repo, CheckOptions(changed=True, select=("IMP001", "IMP003"))
        )

        assert {v.code for v in found} == {"IMP001", "IMP003"}

    def test_deleting_a_required_entry_point_is_a_new_finding(self, repo: Path) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\n  packages:\n    core: {match: app.domain, required: [api]}\n",
            encoding="utf-8",
        )
        write_policy_project(repo, {"app/domain/api.py": ""})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "add package contract")
        (repo / "app/domain/api.py").unlink()

        found = violations(repo, CheckOptions(changed=True, select=("ARC014",)))

        assert [(v.code, v.target_module) for v in found] == [
            ("ARC014", "app.domain.api")
        ]

    def test_new_private_import_is_reported_but_existing_one_is_not(
        self, repo: Path
    ) -> None:
        config = repo / "smelt.yml"
        config.write_text(
            config.read_text()
            + "\n  packages:\n    data: {match: app.infra, public: ['.']}\n",
            encoding="utf-8",
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "declare public facade")
        write_policy_project(
            repo, {"app/application/service.py": "from app.infra import db\n"}
        )

        found = violations(repo, CheckOptions(changed=True, select=("ARC007",)))

        assert [(v.code, v.source_module) for v in found] == [
            ("ARC007", "app.application.service")
        ]

    def test_existing_coverage_gaps_remain_visible_in_changed_mode(
        self, repo: Path
    ) -> None:
        add_rules(repo, {"architecture/uncovered-source": "error"})
        write_policy_project(repo, {"client/api.py": "VALUE = 1\n"})
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "Existing coverage gap")

        [found] = violations(repo, CheckOptions(changed=True))

        assert (found.code, found.path) == ("ARC013", "client/api.py")

    def test_first_python_package_after_a_committed_readme(
        self, tmp_path: Path
    ) -> None:
        _git(tmp_path, "init", "-q")
        write_policy_project(tmp_path, {"README.md": "Project\n"})
        _git(tmp_path, "add", "README.md")
        _git(tmp_path, "commit", "-qm", "Readme")
        write_policy_project(
            tmp_path,
            {
                "smelt.yml": LAYERED_CONFIG,
                "app/__init__.py": "",
                "app/domain/model.py": "from app.infra import db\n",
                "app/infra/db.py": "",
            },
        )

        found = violations(tmp_path, CheckOptions(changed=True))

        assert codes_at(found) == [("ARC001", "app/domain/model.py", 1)]

    def test_invalid_historical_source_is_still_an_error(self, repo: Path) -> None:
        path = repo / "app/domain/model.py"
        path.write_text("def invalid(:\n", encoding="utf-8")
        _git(repo, "add", "app/domain/model.py")
        _git(repo, "commit", "-qm", "Invalid historical source")
        path.write_text("VALUE = 1\n", encoding="utf-8")

        with pytest.raises(AnalysisError, match="syntax error"):
            check(repo, CheckOptions(changed=True))

    def test_replacing_an_indirect_forbidden_target_is_new(self, repo: Path) -> None:
        write_policy_project(
            repo,
            {
                "smelt.yml": LAYERED_CONFIG
                + "    presentation: {path: presentation}\n  import_graph: {transitive: true}\n",
                "app/__init__.py": "from app.infra.db import Db\n",
                "app/infra/db.py": "class Db: pass\n",
                "app/presentation/api.py": "class Api: pass\n",
                "app/domain/model.py": "from app import Db\n",
            },
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "Existing indirect violation")
        write_policy_project(
            repo,
            {
                "app/__init__.py": "from app.presentation.api import Api\n",
                "app/domain/model.py": "from app import Api\n",
            },
        )

        [found] = violations(repo, CheckOptions(changed=True, select=("ARC001",)))

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
            write_policy_project(
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

        found = violations(repo, CheckOptions(changed=True, select=("ARC004",)))

        assert bool(found) is introduced

    def test_pruning_a_growing_cycle_preserves_only_known_edges(
        self, repo: Path
    ) -> None:
        write_policy_project(
            repo,
            {
                "app/domain/a.py": "from app.domain import b\n",
                "app/domain/b.py": "from app.domain import a\n",
                "app/domain/c.py": "VALUE = 1\n",
            },
        )
        options = CheckOptions(select=("ARC004",))
        debt = Debt.from_violations(violations(repo, options), lambda v: "")
        write_policy_project(
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

        assert codes_at(found) == [("ARC001", "app/application/service.py", 1)]

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

        assert codes_at(found) == [("ARC001", "app/application/new.py", 1)]

    def test_base_compares_against_merge_base(self, repo: Path) -> None:
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "app/application/service.py").write_text("from app.infra import db\n")
        _git(repo, "commit", "-q", "-am", "change")

        found = violations(repo, CheckOptions(changed=True, base="main"))

        assert codes_at(found) == [("ARC001", "app/application/service.py", 1)]
