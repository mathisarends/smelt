from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.rules.renames import retired_hint
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

CODES = {"SMT323": "SMT323", "modeling/forbidden-base-class-affix": "SMT323"}


@pytest.mark.parametrize(
    ("key", "hint"),
    [
        (
            "no-base-class-prefix",
            '; renamed to "modeling/forbidden-base-class-affix" in 0.13.0 '
            "(see docs/upgrading.md)",
        ),
        (
            "SMT326",
            '; merged into SMT323 "modeling/forbidden-base-class-affix" in 0.13.0 '
            "(see docs/upgrading.md)",
        ),
        (
            "smt326",
            '; merged into SMT323 "modeling/forbidden-base-class-affix" in 0.13.0 '
            "(see docs/upgrading.md)",
        ),
        ("modeling/forbidden-base-class-affix", ""),
        ("unknown-rule", ""),
    ],
)
def test_retired_hint_points_to_the_replacement(key: str, hint: str) -> None:
    assert retired_hint(key, CODES) == hint


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        (
            "rules: {private-symbol-import: warning}",
            'unknown rule "private-symbol-import"; renamed to '
            '"visibility/foreign-private-access"',
        ),
        (
            "rules: {SMT366: hint}",
            'unknown rule "SMT366"; merged into SMT340 '
            '"complexity/inline-call-argument"',
        ),
    ],
)
def test_retired_rule_configuration_explains_the_migration(
    tmp_path: Path, setting: str, message: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": f"version: 1\nproject: {{root_packages: [app]}}\n{setting}\n",
            "app/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError, match=message):
        check(tmp_path)
