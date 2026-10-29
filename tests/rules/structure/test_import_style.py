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
project: {root_packages: [app, library]}
structure:
  contracts:
    local:
      match: app
      imports: {style: relative, max_relative_level: 1}
"""
OPTIONS = CheckOptions(select=("SMT308",))


def _project(
    tmp_path: Path,
    statement: str,
    *,
    config: str = CONFIG,
    path: str = "app/pkg/service.py",
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "app/pkg/__init__.py": "",
            "app/pkg/models.py": "Record = object()\n",
            "app/domain.py": "Entity = object()\n",
            "library/__init__.py": "Value = 1\n",
            path: statement,
        },
    )


@pytest.mark.parametrize(
    "statement",
    [
        "from .models import Record",
        "from . import models",
        "from app.domain import Entity",
        "from library import Value",
        "import os",
        "import httpx",
        "import app",
        "import app.pkg",
    ],
)
def test_one_dot_policy_accepts_local_relative_and_other_absolute_imports(
    tmp_path: Path, statement: str
) -> None:
    _project(tmp_path, statement)

    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(
    ("statement", "expected_style", "relative_base"),
    [
        ("from app.pkg.models import Record", "relative", ".models"),
        ("from app.pkg import models", "relative", "."),
        ("import app.pkg.models as records", "relative", "."),
        ("from ..domain import Entity", "absolute", None),
        ("from .. import domain", "absolute", None),
        ("from ..pkg.models import Record", "relative", ".models"),
    ],
)
def test_reports_expected_form_and_location(
    tmp_path: Path, statement: str, expected_style: str, relative_base: str | None
) -> None:
    _project(tmp_path, statement)

    found = violations(tmp_path, OPTIONS)

    assert len(found) == 1
    assert found[0].path == "app/pkg/service.py"
    assert found[0].line == 1
    assert found[0].expected is not None
    assert found[0].expected["style"] == expected_style
    assert found[0].expected["relative_base"] == relative_base


def test_relative_depth_is_configurable(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from ..domain import Entity\nfrom app.domain import Entity as E",
        config=CONFIG.replace("level: 1", "level: 2"),
    )

    found = violations(tmp_path, OPTIONS)

    assert [v.line for v in found] == [2]
    assert found[0].expected is not None
    assert found[0].expected["relative_base"] == "..domain"


def test_absolute_mode_rejects_even_single_dot_imports(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from .models import Record\nfrom app.pkg.models import Record as R\nfrom ..domain import Entity",
        config=CONFIG.replace("style: relative", "style: absolute"),
    )

    assert [v.line for v in violations(tmp_path, OPTIONS)] == [1, 3]


def test_nested_package_can_override_import_style(tmp_path: Path) -> None:
    config = (
        CONFIG.replace("style: relative", "style: absolute")
        + "    adapter:\n      match: app.pkg\n      imports: {style: relative}\n"
    )
    _project(tmp_path, "from .models import Record", config=config)
    write_project(tmp_path, {"app/other.py": "from .domain import Entity\n"})

    assert [v.path for v in violations(tmp_path, OPTIONS)] == ["app/other.py"]


def test_conflicting_policies_for_same_package_fail_explicitly(tmp_path: Path) -> None:
    config = (
        CONFIG + "    conflict:\n      match: app\n      imports: {style: absolute}\n"
    )
    _project(tmp_path, "", config=config)

    with pytest.raises(ConfigError, match="conflicting import styles for app"):
        check(tmp_path, OPTIONS)


def test_initializer_uses_its_own_package_as_relative_base(tmp_path: Path) -> None:
    _project(tmp_path, "from .models import Record", path="app/pkg/__init__.py")

    assert violations(tmp_path, OPTIONS) == []


def test_source_root_layout_does_not_affect_relative_depth(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "[app, library]", "[app], source_roots: [source]"
            ),
            "source/app/pkg/service.py": "from .models import Record\nfrom ..domain import Entity",
            "source/app/pkg/models.py": "Record = 1\n",
            "source/app/domain.py": "Entity = 1\n",
        },
    )

    assert [v.line for v in violations(tmp_path, OPTIONS)] == [2]


def test_omitted_policy_preserves_all_styles(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from ..domain import Entity\nfrom app.pkg import models",
        config="version: 1\nproject: {root_packages: [app, library]}\n",
    )

    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(("policy", "count"), [("include", 1), ("ignore", 0)])
def test_type_checking_policy_is_respected(
    tmp_path: Path, policy: str, count: int
) -> None:
    _project(
        tmp_path,
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.pkg.models import Record",
        config=CONFIG + f"\narchitecture:\n  imports: {{type_checking: {policy}}}",
    )

    assert len(violations(tmp_path, OPTIONS)) == count
