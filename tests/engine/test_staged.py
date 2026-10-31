from __future__ import annotations

import json
import os
import runpy
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from smelt.cli import main
from smelt.diagnostics.debt import Debt
from smelt.engine.changes import GitIndex, capture_index
from smelt.engine.check import snippet_reader
from tests.helpers import LAYERED_CONFIG, check, write_project

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

CONFIG = """version: 1
project: {root_packages: [app]}
structure:
  contracts:
    api: {match: app, rules: {internal-public-member: warning}}
"""


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
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/browser.py": "class Browser:\n    def emit(self): pass\n",
            "app/consumer.py": "from .browser import Browser\nBrowser().emit()\n",
        },
    )
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def _check(
    root: Path, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, dict[str, Any]]:
    code = main(
        [
            "check",
            "--staged",
            "--config",
            str(root / "smelt.yaml"),
            "--format",
            "json",
            "--fail-on",
            "warning",
            *args,
        ]
    )
    captured = capsys.readouterr()
    assert not captured.err
    return code, json.loads(captured.out)


def test_staged_change_finds_unchanged_definition_and_matches_uncached(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "app/consumer.py").write_text("")
    _git(repo, "add", "app/consumer.py")
    # An unstaged caller, including an untracked one, must not hide this finding.
    (repo / "app/consumer.py").write_text(
        "from .browser import Browser\nBrowser().emit()"
    )
    (repo / "app/untracked.py").write_text(
        "from .browser import Browser\nBrowser().emit()"
    )

    cold = _check(repo, capsys)
    warm = _check(repo, capsys)
    uncached = _check(repo, capsys, "--no-cache")

    assert cold == warm == uncached
    code, report = cold
    assert code == 1
    assert [(v["code"], v["path"]) for v in report["violations"]] == [
        ("SMT316", "app/browser.py")
    ]
    assert report["scope"]["mode"] == "staged"
    assert report["comparison"]["working_tree"] is False
    assert report["comparison"]["index_hash"]
    assert report["project"]["root"] == repo.resolve().as_posix()
    assert (repo / "app/consumer.py").read_text().endswith("Browser().emit()")


def test_staged_config_is_used_even_when_working_config_is_invalid(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "smelt.yaml").write_text("invalid: [")
    (repo / "app/browser.py").write_text("invalid python :")
    code, report = _check(repo, capsys)
    assert code == 0
    assert report["violations"] == []


def test_reasoned_api_exception_survives_caching_and_staging(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "app/consumer.py").write_text("")
    (repo / "app/browser.py").write_text(
        "class Browser:\n    def emit(self):  # smelt: ignore[SMT316] -- Called by plugin hosts.\n        pass\n"
    )
    _git(repo, "add", "app")
    for _ in range(2):
        code, report = _check(repo, capsys)
        assert code == 0
        assert report["violations"] == []
        assert report["summary"]["suppressed"] == 1


def test_exception_without_reason_blocks_the_commit(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "app/consumer.py").write_text("")
    (repo / "app/browser.py").write_text(
        "class Browser:\n    def emit(self):  # smelt: ignore[SMT316]\n        pass\n"
    )
    _git(repo, "add", "app")
    code, report = _check(repo, capsys)
    assert code == 1
    assert [v["code"] for v in report["violations"]] == ["SMT902"]


def test_first_commit_and_removed_working_config(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = write_project(
        repo / "new",
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/api.py": "def unused(): pass",
        },
    )
    _git(empty, "init", "-q")
    _git(empty, "add", ".")
    (empty / "smelt.yaml").unlink()
    monkeypatch.chdir(empty)
    code = main(["check", "--staged", "--format", "json", "--fail-on", "warning"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["comparison"]["head"] is None
    assert [v["code"] for v in report["violations"]] == ["SMT316"]


def test_nested_config_and_sibling_source_roots(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_project(
        repo,
        {
            "policy/architecture.yaml": CONFIG.replace(
                "root_packages: [app]", "root_packages: [app], source_roots: ['..']"
            ),
            "app/consumer.py": "",
        },
    )
    _git(repo, "add", "policy", "app")
    code = main(
        [
            "check",
            "--staged",
            "--config",
            str(repo / "policy/architecture.yaml"),
            "--format",
            "json",
            "--fail-on",
            "warning",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["project"]["repository_prefix"] == "policy"
    assert [v["path"] for v in report["violations"]] == ["../app/browser.py"]


def test_staged_fixes_and_conflicting_modes_are_usage_errors(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for args in (("--fix",), ("--changed",)):
        code, report = _check(repo, capsys, *args)
        assert code == 2
        assert report["error"]["kind"] == "usage"


def test_staged_python_metadata_determines_version_aware_findings(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "smelt.yaml").write_text(
        CONFIG.replace(
            "rules: {internal-public-member: warning}",
            "future_annotations: when-needed",
        )
    )
    (repo / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.14"\n')
    (repo / "app/browser.py").write_text(
        "from __future__ import annotations\nVALUE = 1\n"
    )
    _git(repo, "add", "smelt.yaml", "pyproject.toml", "app/browser.py")
    (repo / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    code, report = _check(repo, capsys)
    assert code == 1
    assert [v["code"] for v in report["violations"]] == ["SMT310"]


def test_staged_debt_with_arbitrary_filename_is_used(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "app/consumer.py").write_text("")
    outcome = check(repo)
    Debt.from_violations(
        outcome.report.violations, snippet_reader(outcome.context)
    ).write(repo / "accepted.baseline")
    (repo / "smelt.yaml").write_text(CONFIG + "\ndebt: accepted.baseline\n")
    _git(repo, "add", "smelt.yaml", "accepted.baseline", "app")
    (repo / "accepted.baseline").write_text("not JSON")
    code, report = _check(repo, capsys)
    assert code == 0
    assert report["summary"]["in_debt"] == 1


def test_staged_analyses_do_not_materialize_sources(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("staged analysis must not create a temporary source tree")

    monkeypatch.setattr("smelt.engine.check.tempfile.TemporaryDirectory", unexpected)
    (repo / "app/consumer.py").write_text("")
    _git(repo, "add", "app/consumer.py")
    code, report = _check(repo, capsys, "--no-cache")
    assert code == 1
    assert report["violations"][0]["path"] == "app/browser.py"


def test_exact_retry_bypasses_the_engine_and_its_imports(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _check(repo, capsys)

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("an exact staged result must bypass analysis")

    monkeypatch.setattr(
        import_module("smelt.cli.commands.check"), "staged_project", unexpected
    )
    assert _check(repo, capsys) == expected
    executed = subprocess.run(  # noqa: S603 - fixed interpreter and fixture-controlled CLI arguments
        [
            sys.executable,
            "-c",
            "import sys; from smelt.cli import main; "
            "code=main(sys.argv[1:]); "
            "assert 'pydantic' not in sys.modules; assert 'grimp' not in sys.modules; "
            "raise SystemExit(code)",
            "check",
            "--staged",
            "--config",
            str(repo / "smelt.yaml"),
            "--format",
            "json",
            "--fail-on",
            "warning",
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert executed.returncode == expected[0], executed.stderr
    assert json.loads(executed.stdout) == expected[1]


def test_staged_retry_needs_two_git_processes(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(repo)
    expected = _check(repo, capsys)
    calls: list[list[str]] = []
    original = subprocess.run

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert _check(repo, capsys) == expected
    assert len(calls) == 2
    assert "--show-toplevel" in calls[0]
    assert "HEAD" in calls[0]
    assert "ls-files" in calls[1]


def test_head_advance_invalidates_the_exact_result(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "app/consumer.py").write_text("")
    _git(repo, "add", "app/consumer.py")
    assert _check(repo, capsys)[0] == 1
    _git(repo, "commit", "-qm", "accept existing finding")
    assert _check(repo, capsys)[0] == 0
    assert _check(repo, capsys) == _check(repo, capsys, "--no-cache")


def test_corrupt_exact_result_and_cached_blob_fall_back_to_analysis(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = _check(repo, capsys)
    database = next((repo / ".smelt_cache").rglob("results.sqlite3"))
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE results SET payload = ?", (b"broken",))
    assert _check(repo, capsys) == expected
    facts = next((repo / ".smelt_cache").rglob("facts.sqlite3"))
    with closing(sqlite3.connect(facts)) as connection, connection:
        connection.execute("UPDATE objects SET payload = ?", (b"invalid source(",))
    (repo / "app/consumer.py").write_text("")
    _git(repo, "add", "app/consumer.py")
    cached = _check(repo, capsys)
    assert cached[0] == 1
    assert cached == _check(repo, capsys, "--no-cache")


@pytest.mark.parametrize("output", ["json", "sarif"])
def test_document_content_edits_reuse_analysis_with_fresh_index_provenance(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    (repo / "README.md").write_text("before\n")
    _git(repo, "add", "README.md")
    # Also cover a result built from an already cached whole analysis.
    _check(repo, capsys, "--statistics", "--format", output)
    before = _check(repo, capsys, "--format", output)
    (repo / "README.md").write_text("after\n")
    _git(repo, "add", "README.md")

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("unobserved content changes must reuse the result before analysis")

    with monkeypatch.context() as patch:
        patch.setattr(
            import_module("smelt.cli.commands.check"), "staged_project", unexpected
        )
        cached = _check(repo, capsys, "--format", output)
    assert cached != before  # provenance must describe the new captured index
    assert cached == _check(repo, capsys, "--no-cache", "--format", output)


def test_arbitrary_markdown_debt_file_is_an_observed_input(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    Debt([]).write(repo / "accepted.md")
    (repo / "smelt.yaml").write_text(CONFIG + "\ndebt: accepted.md\n")
    _git(repo, "add", "smelt.yaml", "accepted.md")
    assert _check(repo, capsys)[0] == 0
    (repo / "accepted.md").write_text("broken JSON")
    _git(repo, "add", "accepted.md")
    cached = _check(repo, capsys)
    assert cached[0] == 2
    assert cached == _check(repo, capsys, "--no-cache")


def test_inventory_changes_invalidate_reused_results(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _check(repo, capsys)
    (repo / "app/new.py").write_text("def helper(): pass\n")
    _git(repo, "add", "app/new.py")
    cached = _check(repo, capsys)
    assert cached[0] == 1
    assert cached == _check(repo, capsys, "--no-cache")


def test_retained_worker_matches_fresh_checks_after_edits_errors_and_head_changes(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(repo)
    project = Path(__file__).resolve().parents[2]
    # The benchmark harness is a standalone script, not an installed package.
    harness = runpy.run_path(str(project / "scripts/benchmark_session.py"))
    invoke_client = harness["invoke_client"]
    with harness["session"](repo, project) as (endpoint, _):
        _, initial = invoke_client(repo, endpoint)
        assert initial == _check(repo, capsys, "--no-cache")[1]
        (repo / "app/consumer.py").write_text("")
        _git(repo, "add", "app/consumer.py")
        # The retained process must still inspect captured source, not this repair.
        (repo / "app/consumer.py").write_text(
            "from .browser import Browser\nBrowser().emit()\n"
        )
        _, edited = invoke_client(repo, endpoint)
        assert edited["violations"][0]["path"] == "app/browser.py"
        assert edited == _check(repo, capsys, "--no-cache")[1]
        (repo / "smelt.yaml").write_text("broken: [")
        _git(repo, "add", "smelt.yaml")
        with pytest.raises(RuntimeError, match="worker check failed"):
            invoke_client(repo, endpoint)
        (repo / "smelt.yaml").write_text(CONFIG)
        _git(repo, "add", "smelt.yaml")
        _git(repo, "commit", "-qm", "accept finding")
        _, committed = invoke_client(repo, endpoint)
        assert not committed["violations"]
        assert committed == _check(repo, capsys, "--no-cache")[1]


def test_staged_native_import_graph_ignores_unstaged_repairs_and_missing_working_files(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_project(
        repo,
        {
            "smelt.yaml": LAYERED_CONFIG,
            "app/domain/__init__.py": "",
            "app/domain/leak.py": "from app.application import service\n",
            "app/application/__init__.py": "",
            "app/application/service.py": "VALUE = 1\n",
        },
    )
    _git(repo, "add", "smelt.yaml", "app")
    (repo / "app/domain/leak.py").write_text("")
    (repo / "app/application/service.py").unlink()
    cached = _check(repo, capsys)
    assert any(
        v["code"] == "SMT101" and v["path"] == "app/domain/leak.py"
        for v in cached[1]["violations"]
    )
    assert cached == _check(repo, capsys, "--no-cache")


def test_explicit_staged_config_can_be_deleted_from_the_working_tree(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = repo / "nested/smelt.yaml"
    config.parent.mkdir()
    config.write_text(
        CONFIG.replace(
            "root_packages: [app]", "root_packages: [app], source_roots: ['..']"
        )
    )
    _git(repo, "add", "nested/smelt.yaml")
    config.unlink()
    config.parent.rmdir()
    cached = _check(repo, capsys, "--config", str(config))
    assert cached[0] in {0, 1}
    assert cached == _check(repo, capsys, "--config", str(config), "--no-cache")


def test_text_feedback_names_the_decision_and_shows_the_staged_source(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repo / "app/browser.py").write_text(
        "class Browser:\n    def callback(self): pass\n"
    )
    _git(repo, "add", "app/browser.py")
    (repo / "app/browser.py").write_text(
        "class Browser:\n    def _callback(self): pass\n"
    )
    for _ in range(2):
        code = main(
            [
                "check",
                "--staged",
                "--config",
                str(repo / "smelt.yaml"),
                "--fail-on",
                "warning",
                "--no-color",
            ]
        )
        output = capsys.readouterr().out
        assert code == 1
        assert "def callback(self)" in output
        assert "smelt: ignore[SMT316]" in output
        assert "staged Git index" in output


def test_debt_uses_the_captured_index_even_if_another_process_stages_again(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (repo / "app/consumer.py").write_text("")
    outcome = check(repo)
    Debt.from_violations(
        outcome.report.violations, snippet_reader(outcome.context)
    ).write(repo / "accepted.baseline")
    (repo / "smelt.yaml").write_text(CONFIG + "\ndebt: accepted.baseline\n")
    _git(repo, "add", "smelt.yaml", "accepted.baseline", "app")

    def capture(root: Path) -> GitIndex:
        captured = capture_index(root)
        Debt([]).write(repo / "accepted.baseline")
        _git(repo, "add", "accepted.baseline")
        return captured

    monkeypatch.setattr("smelt.engine.result_cache.capture_index", capture)
    code, report = _check(repo, capsys)
    assert code == 0
    assert report["summary"]["in_debt"] == 1
