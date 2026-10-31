from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from smelt.analysis.cache import FactCache
from smelt.analysis.errors import AnalysisError
from smelt.analysis.member_state import MEMBER_STATE, MemberIndexState
from smelt.analysis.members import MemberUsageIndex
from smelt.analysis.retained import RetainedAnalysis, current, retain
from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """version: 1
project: {root_packages: [app]}
structure:
  contracts:
    api: {match: app, rules: {internal-public-member: warning}}
"""


def test_retained_dependencies_skip_deserialization_and_refresh_all_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    retained = RetainedAnalysis()
    with retain(retained):
        check(tmp_path)
    original = FactCache.read

    def read(self: FactCache, key: str, adapter: Any) -> Any:
        if adapter is MEMBER_STATE:
            pytest.fail("a warm worker must retain the validated dependency graph")
        return original(self, key, adapter)

    monkeypatch.setattr(FactCache, "read", read)
    for path, content in [
        ("app/facade.py", "from .other import emit, Base"),
        ("app/other.py", "def emit(): pass\nclass Base: pass"),
        ("app/missing.py", "__all__ = []\ndef helper(): pass"),
        ("app/caller.py", "from .service import emit\nemit()"),
        ("app/other.py", None),
        ("app/caller.py", ""),
        ("app/facade.py", "from .service import *"),
        ("app/caller.py", "from .facade import emit\nemit()"),
    ]:
        target = tmp_path / path
        if content is None:
            target.unlink()
        else:
            target.write_text(content)
        with retain(retained):
            actual = check(tmp_path).report
            expected = check(tmp_path, CheckOptions(use_cache=False)).report
        assert render_json(actual) == render_json(expected), path


def test_failed_update_discards_borrowed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/service.py": "class Api:\n    def emit(self): pass\n",
            "app/caller.py": "def _run(x): x.emit()\n",
        },
    )
    retained = RetainedAnalysis()
    with retain(retained):
        check(tmp_path)
    (tmp_path / "app/caller.py").write_text("# removed caller\n")

    def fail(self: MemberUsageIndex, path: str) -> None:
        raise AnalysisError("interrupted after removing previous contributions")

    with monkeypatch.context() as patch, retain(retained):
        patch.setattr(MemberUsageIndex, "_summary", fail)
        with pytest.raises(AnalysisError, match="interrupted"):
            check(tmp_path)
    with retain(retained):
        actual = check(tmp_path).report
    assert render_json(actual) == render_json(
        check(tmp_path, CheckOptions(use_cache=False)).report
    )
    assert actual.violations


def test_retention_budget_and_request_ownership() -> None:
    state = RetainedAnalysis(max_bytes=10, max_entries=2)
    first, second, third = (
        ("project", "first"),
        ("project", "second"),
        ("other", "third"),
    )
    state.put(first, MemberIndexState(), size=4)
    state.put(second, MemberIndexState(), size=4)
    state.put(third, MemberIndexState(), size=4)
    assert state.take(first) is None
    assert state.take(second) is not None
    state.put(first, MemberIndexState(), size=11)
    assert state.take(first) is None
    with retain(state):
        request = current()
        assert request is not None
        borrowed = request.take(third)
        assert borrowed is not None
        request.put(third, borrowed, size=4)
        assert request.take(third) is None
    with retain(state):
        request = current()
        assert request is not None
        assert request.take(third) is borrowed
    assert current() is None
