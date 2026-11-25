from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from smelt.analysis.errors import AnalysisError
from smelt.cli import main
from smelt.config import ConfigError
from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions
from smelt.engine.fixes import apply_fixes
from tests.helpers import (
    MEMBER_BOUNDARIES,
    check,
    violations,
    write_policy_project,
    write_project,
)

PROJECT = {
    "smelt.yml": """
        version: 2
        project:
          root_packages: [shop]
        architecture:
          layers:
            domain: {path: domain}
            infrastructure: {path: infra, may_depend_on: [domain]}
          packages:
            orders:
              match: shop.domain
              required: [orders, payments]
              public: [orders]
        rules:
          architecture/unclassified-module: warning
          layout/definition-order: warning
    """,
    "shop/__init__.py": '"""The shop."""\n',
    "shop/domain/__init__.py": "",
    "shop/domain/orders.py": (
        "from shop.infra.store import Store\n\n\nclass Order:\n    store: Store\n"
    ),
    "shop/infra/__init__.py": "",
    "shop/infra/store.py": "class Store:\n    pass  # smelt: ignore[no-such-rule]\n",
    "shop/helpers.py": (
        "def _helper() -> None:\n    pass\n\n\ndef helper() -> None:\n    _helper()\n"
    ),
}
BASE = CheckOptions(show_coverage=True)


def test_source_that_does_not_parse_fails_only_rules_reading_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [shop]}\n",
            "shop/__init__.py": "",
            "shop/broken.py": "total = = 0\n",
        },
    )

    names_only = check(tmp_path, replace(BASE, select=("LAY001",)))

    assert names_only.report.violations == []
    with pytest.raises(AnalysisError, match=r"^shop/broken\.py:1: syntax error: "):
        check(tmp_path, replace(BASE, select=("LAY004",)))


def test_equally_specific_conflicting_policies_are_a_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = PROJECT["smelt.yml"].replace(
        "rules:",
        "    bundle: {match: shop.domain, min_imports_to_bundle: 2}\n"
        "            rival: {match: shop.domain, min_imports_to_bundle: 3}\n"
        "        rules:",
    )
    write_project(tmp_path, {**PROJECT, "smelt.yml": config})

    with pytest.raises(ConfigError, match="equally specific") as raised:
        check(tmp_path, replace(BASE, select=("IMP001",)))

    [issue] = raised.value.issues
    assert issue.path == "architecture.packages.rival.min_imports_to_bundle"


def test_fix_leaves_a_source_edited_since_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(tmp_path, PROJECT)
    options = replace(BASE, select=("LAY004",))
    outcome = check(tmp_path, options)
    helpers = tmp_path / "shop/helpers.py"
    edited = helpers.read_text() + "# Edited after the check.\n"
    helpers.write_text(edited)

    _, fixed = apply_fixes(outcome, lambda: check(tmp_path, options))

    assert fixed.files == []
    assert "changed since the check" in fixed.skipped[0]["reason"]
    assert helpers.read_text() == edited


def test_member_architecture_follows_only_its_own_import_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "  import_graph: {transitive: true}\n",
            "packages/core/src/core/model.py": "import requests\n",
            "services/api/smelt.yml": """
                architecture:
                  layers:
                    domain: {path: domain, third_party: {default: deny}}
                    infra: {path: infra}
                """,
            "services/api/src/api/domain/remote.py": "import core.model\n",
            "services/api/src/api/domain/local.py": "import api.infra.client\n",
            "services/api/src/api/infra/client.py": "import requests\n",
        },
    )
    found = violations(tmp_path, CheckOptions(select=("ARC003",)))

    assert [finding.source_module for finding in found] == ["api.domain.local"]


# The edge-case projects whose reports the Python engine once confirmed. Their
# golden reports keep those results; `SMELT_UPDATE_GOLDEN=1` rewrites them.
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIXTURES / "golden"
GOLDEN_PROJECTS = sorted(
    path.name for path in FIXTURES.glob("rust_parity*") if path.is_dir()
)
GOLDEN_MODES = {
    "configured": BASE,
    "strict": replace(BASE, select=("ALL",), strict_coverage=True),
}
GOLDEN_GIT_MODES = {
    "changed": ("--changed", "--select", "ALL"),
    "staged": ("--staged", "--select", "ALL", "--strict-coverage"),
}
# The parser's explanation of a syntax error and the guide's release vary.
_VOLATILE = (
    (re.compile(r"(: syntax error): .*", re.DOTALL), r"\1"),
    (re.compile(r"/blob/[^/]+/docs/upgrading"), "/blob/REF/docs/upgrading"),
)


@pytest.mark.parametrize("mode", GOLDEN_MODES)
@pytest.mark.parametrize("project", GOLDEN_PROJECTS)
def test_reports_its_golden_findings(tmp_path: Path, project: str, mode: str) -> None:
    root = _copy(project, tmp_path)
    try:
        outcome = check(root, GOLDEN_MODES[mode])
    except (AnalysisError, ConfigError) as error:
        report: dict[str, Any] = {"error": f"{type(error).__name__}: {error}"}
    else:
        report = json.loads(render_json(outcome.report, show_coverage=True))

    _assert_golden(f"{project}.{mode}", report)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("mode", GOLDEN_GIT_MODES)
@pytest.mark.parametrize("project", GOLDEN_PROJECTS)
def test_reports_its_golden_git_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project: str,
    mode: str,
) -> None:
    root = _copy(project, tmp_path)
    _edit_history(root)
    monkeypatch.chdir(root)

    main(["check", *GOLDEN_GIT_MODES[mode], "--show-coverage", "--format", "json"])
    captured = capsys.readouterr()
    try:
        report = json.loads(captured.out)
    except ValueError:
        report = {"error": captured.err.strip()}

    _assert_golden(f"{project}.{mode}", report)


def _copy(project: str, tmp_path: Path) -> Path:
    root = tmp_path / project
    shutil.copytree(
        FIXTURES / project, root, ignore=shutil.ignore_patterns("__pycache__")
    )
    return root


def _edit_history(root: Path) -> None:
    """Commit the project, then edit, copy and delete sources and stage part of it."""
    _git(root, "init", "--quiet")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--message", "base")
    # Paths compare case-insensitively on Windows; strings order alike everywhere.
    sources = sorted(
        (path for path in root.rglob("*.py") if ".git" not in path.parts),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for index, path in enumerate(sources):
        if index % 3 == 0:
            # Shifted lines must still match the base commit's findings.
            path.write_bytes(b"\n" + path.read_bytes())
        elif index % 3 == 1:
            shutil.copyfile(path, path.with_name(f"{path.stem}_copy.py"))
        elif path.name != "__init__.py":
            path.unlink()
    _git(root, "add", "--all")
    for path in sources[::3]:
        # Unstaged edits count for --changed only.
        path.write_bytes(path.read_bytes() + b"\nfrom os import path as _path\n")


def _git(root: Path, *arguments: str) -> None:
    identity = ("-c", "user.name=golden", "-c", "user.email=golden@example.invalid")
    subprocess.run(  # noqa: S603 - fixed Git arguments
        ["git", *identity, *arguments],  # noqa: S607 - Git from PATH
        cwd=root,
        check=True,
        capture_output=True,
    )


def _assert_golden(name: str, report: dict[str, Any]) -> None:
    actual = _summary(report)
    golden = GOLDEN / f"{name}.txt"
    if os.environ.get("SMELT_UPDATE_GOLDEN"):
        golden.parent.mkdir(exist_ok=True)
        golden.write_text(actual, encoding="utf-8", newline="\n")
    assert actual == golden.read_text(encoding="utf-8")


def _summary(report: dict[str, Any]) -> str:
    """The findings and counts of a report, one line each, in a stable order."""
    if "error" in report:
        return _stable(f"error: {report['error']}") + "\n"
    coverage = report.get("coverage", {})
    summary = report["summary"]
    lines = [
        f"modules: {summary['modules']}, suppressed: {summary['suppressed']},"
        f" in debt: {summary['in_debt']}",
        f"coverage: {json.dumps(coverage.get('counts'), sort_keys=True)}"
        f" complete={coverage.get('complete')}",
    ]
    findings = sorted(
        f"{v['code']} {v.get('path')}:{v.get('line')}:{v.get('column')}"
        f" {v['severity']}{' fix' if v.get('fix') else ''}"
        f"{' member=' + v['member'] if v.get('member') else ''} {_stable(v['message'])}"
        for v in report["violations"]
    )
    return "\n".join([*lines, *findings]) + "\n"


def _stable(text: str) -> str:
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return text
