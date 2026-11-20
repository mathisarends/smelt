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
            "smelt.yml": f"""
            version: 2
            project:
              root_packages: [acme]
              source_roots: {source_roots}
            architecture:
              packages:
                library:
                  match: acme
                  required: [commands, records]
                  public: [commands]
        """,
            f"{roots[0]}/acme/commands.py": "",
            f"{roots[1]}/acme/records.py": "",
        },
    )
    ctx = AnalysisContext(tmp_path, load_config(tmp_path / "smelt.yml").config)

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
            "smelt.yml": """
            version: 2
            project: {root_packages: [lib]}
            architecture:
              packages:
                whole: {match: lib, public: [api.**]}
                nested: {match: lib.api, public: ['.']}
        """,
            "lib/api/__init__.py": "",
            "lib/api/impl.py": "",
        },
    )
    ctx = AnalysisContext(tmp_path, load_config(tmp_path / "smelt.yml").config)

    assert {entry["name"] for entry in ctx.contracts.describe("lib.api.impl")} == {
        "nested",
        "whole",
    }
    assert {entry["name"] for entry in ctx.contracts.describe("lib.unrelated")} == {
        "whole"
    }
    assert ctx.contracts.describe("other") == []


def test_policy_for_prefers_the_closest_contract_then_structure(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": """
            version: 2
            project: {root_packages: [app]}
            rules:
              complexity/excess-positional-parameters: {severity: warning, max: 3}
            style:
              overrides:
                api:
                  match: app.api
                  rules:
                    complexity/excess-positional-parameters: {max: 2}
        """,
            "app/__init__.py": "",
            "app/core.py": "",
            "app/api/__init__.py": "",
            "app/api/routes.py": "",
        },
    )
    contracts = AnalysisContext(
        tmp_path, load_config(tmp_path / "smelt.yml").config
    ).contracts

    nested = contracts.policy_for("app.api.routes", "max_positional_parameters")
    outer = contracts.policy_for("app.core", "max_positional_parameters")

    assert nested is not None
    assert (nested.name, nested.origin) == ("api", 'contract "api"')
    assert outer is not None
    assert (outer.name, outer.origin, outer.project_wide) == (
        "style",
        "style",
        True,
    )
    assert contracts.policy_for("app.core", "dataclass_options") is None
    assert contracts.threshold("app.core", "max_comprehension_loops") == (1, None)
    assert contracts.threshold("app.api.routes", "max_positional_parameters") == (
        2,
        nested,
    )
    assert [entry["name"] for entry in contracts.describe("app.core")] == []
