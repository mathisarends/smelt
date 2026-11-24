from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from smelt.analysis.errors import AnalysisError
from smelt.cli import main
from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

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
BASE = CheckOptions(use_cache=False, show_coverage=True)


@pytest.mark.parametrize(
    "options",
    [
        BASE,
        replace(BASE, select=("ALL",), strict_coverage=True),
        replace(BASE, select=("ALL",), ignore=("ARC",)),
        replace(BASE, paths=("shop/domain",)),
    ],
    ids=["configured", "strict", "ignored", "scoped"],
)
def test_reports_like_the_python_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: CheckOptions
) -> None:
    write_project(tmp_path, PROJECT)
    expected = render_json(check(tmp_path, options).report, show_coverage=True)

    monkeypatch.setenv("SMELT_CORE", "native")
    actual = render_json(check(tmp_path, options).report, show_coverage=True)

    assert json.loads(actual) == json.loads(expected)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    "arguments",
    [("--changed",), ("--changed", "--select", "ALL"), ("--staged",)],
    ids=["changed", "changed-all", "staged"],
)
def test_reports_git_snapshots_like_the_python_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
) -> None:
    write_project(tmp_path, PROJECT)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    # Shifted lines keep their findings; the new module and the unstaged edit add some.
    orders = tmp_path / "shop/domain/orders.py"
    orders.write_text("\n" + orders.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "shop/domain/payments.py").write_text(
        "def pay() -> None:\n    pass\n\n\nimport os\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    helpers = tmp_path / "shop/helpers.py"
    helpers.write_text(helpers.read_text(encoding="utf-8") + "\n\nimport os\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMELT_DAEMON", "0")

    expected = _cli_report(capsys, arguments)
    monkeypatch.setenv("SMELT_CORE", "native")
    actual = _cli_report(capsys, arguments)

    assert expected["violations"]
    assert actual == expected


def _cli_report(
    capsys: pytest.CaptureFixture[str], arguments: tuple[str, ...]
) -> dict[str, Any]:
    main(["check", *arguments, "--no-cache", "--show-coverage", "--format", "json"])
    report: dict[str, Any] = json.loads(capsys.readouterr().out)
    return report


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        timeout=10,
    )


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
    monkeypatch.setenv("SMELT_CORE", "native")

    names_only = check(tmp_path, replace(BASE, select=("LAY001",)))

    assert names_only.report.violations == []
    with pytest.raises(AnalysisError, match=r"^shop/broken\.py:1: syntax error: "):
        check(tmp_path, replace(BASE, select=("LAY004",)))
