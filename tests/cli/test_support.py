from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.cli.support import load_project_config
from smelt.config import ConfigError
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def test_schema_errors_list_unknown_rule_selectors_in_the_same_report(
    tmp_path: Path,
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n"
            "structure: {forbidden_names: [utils]}\n"
            "rules: {import-cycle: error, architecture/import-cycle: error}\n"
            "ignore: [{rule: private-test-access, paths: [app], reason: x}]\n",
            "app/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError) as caught:
        load_project_config(None, root)

    assert [issue.path for issue in caught.value.issues] == [
        "structure",
        "rules.import-cycle",
        "ignore[0].rule",
    ]


def test_valid_rule_selectors_add_nothing_to_schema_errors(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n"
            "structure: {}\nrules: {architecture/import-cycle: error}\n",
            "app/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError) as caught:
        load_project_config(None, root)

    assert [issue.path for issue in caught.value.issues] == ["structure"]
