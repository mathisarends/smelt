from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.private_attributes import (
    PrivateAttributeIndex,
)
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
