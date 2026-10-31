from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING

from smelt.analysis.cache import FactCache
from smelt.analysis.context import AnalysisContext
from smelt.analysis.members import MemberUsageIndex
from smelt.analysis.parsing import AnalysisError, AstCache
from smelt.config import load_config
from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions
from smelt.rules.structure.members import InternalPublicMember
from tests.helpers import LAYERED_CONFIG, check, write_project

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


CONFIG = """version: 1
project: {root_packages: [app]}
structure:
  contracts:
    api: {match: app, rules: {internal-public-member: warning}}
"""


def test_content_changes_invalidate_even_with_identical_size_and_mtime(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/service.py": "class Browser:\n    def emit(self): pass",
            "app/caller.py": "def _run(x): x.emit()",
        },
    )
    assert not check(tmp_path).report.violations
    caller = tmp_path / "app/caller.py"
    previous = caller.stat()
    caller.write_text("def _run(x): x.skip()")
    os.utime(caller, ns=(previous.st_atime_ns, previous.st_mtime_ns))

    warm = check(tmp_path).report
    uncached = check(tmp_path, CheckOptions(use_cache=False)).report

    assert render_json(warm) == render_json(uncached)
    assert [(v.code, v.path) for v in warm.violations] == [("SMT316", "app/service.py")]


def test_warm_check_reuses_rule_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/api.py": "def public(): pass",
        },
    )
    expected = render_json(check(tmp_path).report)

    def unexpected(*args: object) -> None:
        raise AssertionError("warm check must reuse the analysis")

    monkeypatch.setattr(InternalPublicMember, "check", unexpected)
    assert render_json(check(tmp_path).report) == expected


def test_corrupt_cache_falls_back_to_full_analysis(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/api.py": "def public(): pass",
        },
    )
    expected = render_json(check(tmp_path).report)
    files = list((tmp_path / ".smelt_cache").rglob("*.sqlite3"))
    assert files
    for path in files:
        path.write_bytes(b"{truncated")
    assert render_json(check(tmp_path).report) == expected


def test_busy_cache_writer_does_not_change_the_check(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/api.py": "def public(): pass",
        },
    )
    check(tmp_path)
    database = next((tmp_path / ".smelt_cache").rglob("*.sqlite3"))
    writer = sqlite3.connect(database)
    try:
        writer.execute("BEGIN IMMEDIATE")
        (tmp_path / "app/api.py").write_text("def another_public(): pass")
        actual = render_json(check(tmp_path).report)
        expected = render_json(check(tmp_path, CheckOptions(use_cache=False)).report)
        assert actual == expected
    finally:
        writer.close()


def test_explicit_selection_cannot_reuse_a_package_only_result(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace("match: app,", "match: app.api,"),
            "app/__init__.py": "",
            "app/api/__init__.py": "",
            "app/other.py": "def helper(): pass",
        },
    )
    initial = check(tmp_path)
    ignore = tuple(rule.code for rule, _ in initial.active if rule.code != "SMT316")
    assert check(tmp_path, CheckOptions(ignore=ignore)).report.violations == []
    options = CheckOptions(select=("internal-public-member",), ignore=ignore)
    selected = check(tmp_path, options).report
    assert [(v.code, v.path) for v in selected.violations] == [
        ("SMT316", "app/other.py")
    ]
    assert render_json(selected) == render_json(
        check(
            tmp_path,
            CheckOptions(select=options.select, ignore=ignore, use_cache=False),
        ).report
    )


def test_policy_changes_and_deletions_invalidate_cached_findings(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/api.py": "def public(): pass",
        },
    )
    assert check(tmp_path).report.violations
    (tmp_path / "smelt.yaml").write_text(
        CONFIG.replace("internal-public-member: warning", "internal-public-member: off")
    )
    assert not check(tmp_path).report.violations
    (tmp_path / "smelt.yaml").write_text(CONFIG)
    (tmp_path / "app/api.py").unlink()
    assert not check(tmp_path).report.violations


def test_new_uncovered_source_cannot_hide_in_a_warm_cache(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": LAYERED_CONFIG,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
        },
    )
    options = CheckOptions(strict_coverage=True)
    assert not check(tmp_path, options).report.violations
    (tmp_path / "forgotten.py").write_text("VALUE = 1")
    assert [(v.code, v.path) for v in check(tmp_path, options).report.violations] == [
        ("SMT306", "forgotten.py")
    ]


def test_cached_member_facts_only_parse_the_changed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/service.py": "class Browser:\n    def emit(self): pass",
            "app/caller.py": "def run(x): x.emit()",
        },
    )
    config = load_config(tmp_path / "smelt.yaml").config
    cache = FactCache(tmp_path / ".smelt_cache")
    _ = AnalysisContext(tmp_path, config, cache=cache).member_usage.members
    cache.close()
    (tmp_path / "app/caller.py").write_text("def run(x): pass")
    original = AstCache.parse
    parsed: set[str] = set()

    def parse(self: AstCache, path: str) -> object:
        parsed.add(path)
        if path != "app/caller.py":
            raise AnalysisError(f"unchanged file reparsed: {path}")
        return original(self, path)

    monkeypatch.setattr(AstCache, "parse", parse)
    cache = FactCache(tmp_path / ".smelt_cache")
    members = AnalysisContext(tmp_path, config, cache=cache).member_usage.members
    cache.close()
    assert parsed == {"app/caller.py"}
    emit = next(member for member in members if member.node.name == "emit")
    assert not emit.external_use


def test_incremental_members_only_rebuild_changed_contributions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/service.py": "class Browser:\n    def emit(self): pass",
            "app/other.py": "class Other:\n    def unrelated(self): pass",
            "app/caller.py": "def _run(x): x.emit()",
        },
    )
    check(tmp_path)
    original = MemberUsageIndex._summary
    rebuilt: list[str] = []

    def summary(self: MemberUsageIndex, path: str) -> object:
        rebuilt.append(path)
        return original(self, path)

    monkeypatch.setattr(MemberUsageIndex, "_summary", summary)
    (tmp_path / "app/caller.py").write_text("def _run(x): x.skip()")
    cached = check(tmp_path).report
    assert rebuilt == ["app/caller.py"]
    assert render_json(cached) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )
    assert {v.path for v in cached.violations} == {"app/service.py", "app/other.py"}


def test_incremental_members_track_reexports_inheritance_and_missing_namespaces(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/service.py": "__all__ = []\ndef emit(): pass\nclass Base:\n    def run(self): pass",
            "app/facade.py": "from .service import emit, Base",
            "app/caller.py": "from .facade import emit, Base\nfrom .missing import helper\nemit()\nclass Child(Base): pass\nhelper()",
        },
    )
    check(tmp_path)
    edits: list[tuple[str, str | None]] = [
        ("app/facade.py", "from .other import emit, Base"),
        ("app/other.py", "def emit(): pass\nclass Base: pass"),
        ("app/missing.py", "__all__ = []\ndef helper(): pass"),
        ("app/caller.py", "from .service import emit\nemit()"),
        ("app/other.py", None),
        ("app/caller.py", ""),
        ("app/facade.py", "from .service import *"),
        ("app/caller.py", "from .facade import emit\nemit()"),
    ]
    for path, content in edits:
        target = tmp_path / path
        if content is None:
            target.unlink()
        else:
            target.write_text(content)
        assert render_json(check(tmp_path).report) == render_json(
            check(tmp_path, CheckOptions(use_cache=False)).report
        ), path


def test_incremental_members_keep_duplicate_same_line_uses(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/service.py": "class Browser:\n    def emit(self): pass\n    def _run(self): self.emit(); self.emit()",
            "app/caller.py": "def _run(x): x.emit()",
        },
    )
    check(tmp_path)
    (tmp_path / "app/caller.py").write_text("")
    assert render_json(check(tmp_path).report) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )
