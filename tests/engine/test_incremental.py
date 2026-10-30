from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.members import MemberUsageIndex
from smelt.analysis.parsing import AstCache
from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, check, write_project

if TYPE_CHECKING:
    import ast
    from pathlib import Path

    import pytest

    from smelt.analysis.members import Member, MemberState, _FileFacts


def test_changed_reexport_rechecks_unchanged_definitions_but_reuses_unrelated_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\nrules: {internal-public-member: warning}\n",
            "app/__init__.py": "",
            "app/a.py": "def helper(): pass\n",
            "app/b.py": "def helper(): pass\n",
            "app/facade.py": "from .a import helper\n",
            "app/caller.py": "from .facade import helper\nhelper()\n",
            "app/unrelated.py": "def stable(): pass\n",
        },
    )
    before = check(tmp_path).report
    assert {v.path for v in before.violations} == {"app/b.py", "app/unrelated.py"}
    rebuilt: list[str] = []
    original = MemberUsageIndex._resolve_file

    def resolve(
        self: MemberUsageIndex, file: _FileFacts, states: list[MemberState]
    ) -> list[Member]:
        rebuilt.append(file.path)
        return original(self, file, states)

    monkeypatch.setattr(MemberUsageIndex, "_resolve_file", resolve)
    (tmp_path / "app/facade.py").write_text("from .b import helper\n")
    after = check(tmp_path).report
    assert {v.path for v in after.violations} == {"app/a.py", "app/unrelated.py"}
    assert {"app/a.py", "app/b.py"} <= set(rebuilt)
    assert "app/unrelated.py" not in rebuilt
    assert render_json(after) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )


def test_attribute_facts_and_test_constructor_specs_reuse_unchanged_production_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\nrules: {test-only-public-attribute: warning}\n",
            "app/__init__.py": "",
            "app/worker.py": "class Worker:\n    def __init__(self): self.pending = 0\n",
            "app/neighbor.py": "VALUE = 1\n",
            "tests/test_worker.py": "from app.worker import Worker\ndef test_worker():\n    assert Worker().pending == 0\n",
        },
    )
    assert [v.code for v in check(tmp_path).report.violations] == ["SMT430"]
    parsed: list[str] = []
    original = AstCache.parse

    def parse(self: AstCache, path: str) -> ast.Module:
        parsed.append(path)
        return original(self, path)

    monkeypatch.setattr(AstCache, "parse", parse)
    (tmp_path / "app/neighbor.py").write_text("VALUE = 2\n")
    cached = check(tmp_path).report
    assert "app/worker.py" not in parsed
    assert not any(path.startswith("tests/") for path in parsed)
    assert render_json(cached) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )


def test_unchanged_test_quality_decisions_do_not_reparse_test_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\nrules: {test-without-outcome-check: warning}\n",
            "app/__init__.py": "",
            "app/worker.py": "class Worker: pass\n",
            "app/neighbor.py": "VALUE = 1\n",
            "tests/test_worker.py": "def test_worker():\n    assert 1 == 1\n",
        },
    )
    check(tmp_path)
    parsed: list[str] = []
    original = AstCache.parse

    def parse(self: AstCache, path: str) -> ast.Module:
        parsed.append(path)
        return original(self, path)

    monkeypatch.setattr(AstCache, "parse", parse)
    (tmp_path / "app/neighbor.py").write_text("VALUE = 2\n")
    cached = check(tmp_path).report
    assert not any(path.startswith("tests/") for path in parsed)
    assert render_json(cached) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )


def test_fixture_changes_invalidate_unchanged_test_quality_decisions(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\nrules: {test-without-outcome-check: warning}\n",
            "app/__init__.py": "",
            "tests/conftest.py": "import pytest\nfrom unittest.mock import Mock\n@pytest.fixture\ndef client():\n    return Mock()\n",
            "tests/test_client.py": "def test_client(client):\n    client.assert_called_once()\n",
        },
    )
    assert not check(tmp_path).report.violations
    fixture = tmp_path / "tests/conftest.py"
    fixture.write_text(fixture.read_text().replace("return Mock()", "return object()"))
    cached = check(tmp_path).report
    assert [v.code for v in cached.violations] == ["SMT424"]
    assert render_json(cached) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )


def test_native_import_scans_and_details_only_parse_changed_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": LAYERED_CONFIG,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/application/__init__.py": "",
            "app/application/service.py": "from app import domain\n",
        },
    )
    check(tmp_path)
    parsed: list[str] = []
    original = AstCache.parse

    def parse(self: AstCache, path: str) -> ast.Module:
        parsed.append(path)
        return original(self, path)

    monkeypatch.setattr(AstCache, "parse", parse)
    (tmp_path / "app/application/service.py").write_text(
        "from app import domain\nVALUE = 1\n"
    )
    cached = check(tmp_path).report
    assert set(parsed) == {"app/application/service.py"}
    assert render_json(cached) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )
