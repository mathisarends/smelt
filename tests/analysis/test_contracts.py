from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.context import AnalysisContext
from smelt.config import load_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("source_roots", ["[src]", "[left, right]"])
def test_contracts_follow_arbitrary_packages_and_namespace_portions(
    tmp_path: Path, source_roots: str
) -> None:
    roots = ["src", "src"] if source_roots == "[src]" else ["left", "right"]
    write_project(
        tmp_path,
        {
            "smelt.yaml": f"""
            version: 1
            project:
              root_packages: [acme]
              source_roots: {source_roots}
            structure:
              contracts:
                library:
                  match: acme
                  required: [commands, records]
                  public: [commands]
        """,
            f"{roots[0]}/acme/commands.py": "",
            f"{roots[1]}/acme/records.py": "",
        },
    )
    ctx = AnalysisContext(tmp_path, load_config(tmp_path / "smelt.yaml").config)

    described = ctx.contracts.describe("acme.records")

    assert described == [
        {
            "name": "library",
            "package": "acme",
            "match": "acme",
            "rules": {},
            "required": ["acme.commands", "acme.records"],
            "missing": [],
            "public": ["acme.commands"],
            "missing_public": [],
            "target_public": False,
        }
    ]


def test_overlapping_contracts_remain_visible_in_context(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": """
            version: 1
            project: {root_packages: [lib]}
            structure:
              contracts:
                whole: {match: lib, public: [api.**]}
                nested: {match: lib.api, public: ['.']}
        """,
            "lib/api/__init__.py": "",
            "lib/api/impl.py": "",
        },
    )
    ctx = AnalysisContext(tmp_path, load_config(tmp_path / "smelt.yaml").config)

    assert {entry["name"] for entry in ctx.contracts.describe("lib.api.impl")} == {
        "nested",
        "whole",
    }
    assert {entry["name"] for entry in ctx.contracts.describe("lib.unrelated")} == {
        "whole"
    }
    assert ctx.contracts.describe("other") == []
