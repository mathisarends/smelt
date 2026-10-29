from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


CONFIG = """
version: 1
project:
  root_packages: [app]
structure:
  contracts:
    component:
      match: app.parts.*
      required: [api, core.models]
"""
OPTIONS = CheckOptions(select=("SMT307",))


def test_missing_modules_are_reported_per_matching_package(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/parts/one/__init__.py": "",
            "app/parts/one/api.py": "",
            "app/parts/two/__init__.py": "",
            "app/parts/two/core/models.py": "",
        },
    )

    found = violations(tmp_path, OPTIONS)

    assert {(v.source_module, v.target_module) for v in found} == {
        ("app.parts.one", "app.parts.one.core.models"),
        ("app.parts.two", "app.parts.two.api"),
    }
    assert all(v.code == "SMT307" for v in found)
    assert all(v.expected and v.expected["contract"] == "component" for v in found)


@pytest.mark.parametrize("layout", ["module", "package", "namespace"])
def test_required_entry_accepts_modules_and_packages(
    tmp_path: Path, layout: str
) -> None:
    api_path = {
        "module": "api.py",
        "package": "api/__init__.py",
        "namespace": "api/service.py",
    }[layout]
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/parts/one/__init__.py": "",
            f"app/parts/one/{api_path}": "",
            "app/parts/one/core/models.py": "",
        },
    )

    assert violations(tmp_path, OPTIONS) == []


def test_excluded_module_does_not_fulfill_contract(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "root_packages: [app]", "root_packages: [app]\n  exclude: ['**/api.py']"
            ),
            "app/parts/one/__init__.py": "",
            "app/parts/one/api.py": "",
            "app/parts/one/core/models.py": "",
        },
    )

    assert [v.target_module for v in violations(tmp_path, OPTIONS)] == [
        "app.parts.one.api"
    ]


def test_nonmatching_package_and_deeper_descendants_are_unaffected(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/parts/one/api.py": "",
            "app/parts/one/core/models.py": "",
            "app/parts/one/nested/task.py": "",
            "app/other/__init__.py": "",
        },
    )

    assert violations(tmp_path, OPTIONS) == []


def test_public_entry_points_must_exist_without_duplicate_findings(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "required: [api, core.models]",
                "required: [api]\n      public: [api, commands.**]",
            ),
            "app/parts/one/__init__.py": "",
        },
    )

    assert {v.target_module for v in violations(tmp_path, OPTIONS)} == {
        "app.parts.one.api",
        "app.parts.one.commands.**",
    }


def test_private_package_does_not_require_public_entry_point(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace("required: [api, core.models]", "public: []"),
            "app/parts/one/__init__.py": "",
        },
    )

    assert violations(tmp_path, OPTIONS) == []


def test_package_match_typo_is_a_config_error(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/other/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError, match="matches no analyzed package"):
        check(tmp_path, OPTIONS)


def test_no_contract_keeps_existing_behavior(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}",
            "app/__init__.py": "",
        },
    )

    assert violations(tmp_path, OPTIONS) == []
