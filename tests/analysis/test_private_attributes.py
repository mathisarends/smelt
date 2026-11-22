from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.cache import FactCache
from smelt.analysis.context import AnalysisContext
from smelt.analysis.private_attributes import (
    PrivateAttributeAccess,
    PrivateAttributeIndex,
)
from smelt.config import load_config
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path


def test_ownership_facts_preserve_definition_and_receiver_provenance(
    tmp_path: Path,
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/base.py": "class Store:\n    _data = None\n",
            "app/derived.py": "from .base import Store\nclass Derived(Store): pass\n",
            "app/client.py": "from .derived import Derived\ndef read(store: Derived):\n    return store._data\n",
        },
    )
    outcome = check(root, CheckOptions(select=("VIS005",)))
    source = outcome.context.files.source_for_path("app/client.py")
    assert source is not None
    [fact] = PrivateAttributeIndex(
        outcome.context.syntax, outcome.context.files
    ).accesses(source)
    assert (
        fact.owner_module,
        fact.owner_class,
        fact.receiver_type,
        fact.operation,
    ) == ("app.base", "app.base.Store", "app.derived.Derived", "read")
    assert (fact.line, fact.attribute) == (3, "_data")


_PROJECT = {
    "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
    "app/__init__.py": "",
    "app/base.py": "class Store:\n    _data = None\n",
    "app/client.py": "from .base import Store\ndef read(store: Store):\n    return store._data\n",
    "app/other.py": "def helper(): pass\n",
}


def _cached_accesses(root: Path) -> tuple[PrivateAttributeAccess, ...]:
    config = load_config(root / "smelt.yml").config
    with closing(FactCache(root / ".smelt_cache")) as cache:
        ctx = AnalysisContext(root, config, cache=cache)
        source = ctx.files.source_for_path("app/client.py")
        assert source is not None
        return PrivateAttributeIndex(ctx.syntax, ctx.files).accesses(source)


def test_accesses_are_reused_after_edits_to_modules_resolution_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = write_project(tmp_path, _PROJECT)
    expected = _cached_accesses(root)
    (root / "app/other.py").write_text("def renamed(): pass\n", encoding="utf-8")

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("accesses were resolved again after an unrelated edit")

    monkeypatch.setattr(PrivateAttributeIndex, "_scope", unexpected)
    assert _cached_accesses(root) == expected


def test_accesses_are_resolved_again_when_a_module_they_read_changes(
    tmp_path: Path,
) -> None:
    root = write_project(tmp_path, _PROJECT)
    assert [access.attribute for access in _cached_accesses(root)] == ["_data"]
    (root / "app/base.py").write_text("class Store:\n    pass\n", encoding="utf-8")

    assert _cached_accesses(root) == ()
