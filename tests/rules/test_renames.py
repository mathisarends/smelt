from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.config.errors import UPGRADE_GUIDE_URL
from smelt.rules.renames import retired_hint
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

CODES = {"MOD002": "MOD002", "modeling/forbidden-base-class-affix": "MOD002"}


@pytest.mark.parametrize(
    ("key", "hint"),
    [
        (
            "no-base-class-prefix",
            '; renamed to "modeling/forbidden-base-class-affix" in 0.13.0 '
            f"(see {UPGRADE_GUIDE_URL})",
        ),
        (
            "SMT326",
            '; merged into MOD002 "modeling/forbidden-base-class-affix" in 0.13.0 '
            f"(see {UPGRADE_GUIDE_URL})",
        ),
        (
            "information-hiding",
            "; the information-hiding selector was removed in 0.13.0. List "
            "visibility/internal-public-member, "
            "visibility/internal-public-module-symbol, "
            "visibility/internal-public-attribute, "
            "visibility/test-only-public-function, "
            "visibility/test-only-public-attribute individually with the same "
            f"severity (see {UPGRADE_GUIDE_URL})",
        ),
        ("modeling/forbidden-base-class-affix", ""),
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
            'unknown rule "SMT366"; merged into CPX002 '
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
            "smelt.yml": f"version: 2\nproject: {{root_packages: [app]}}\n{setting}\n",
            "app/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError, match=message):
        check(tmp_path)
