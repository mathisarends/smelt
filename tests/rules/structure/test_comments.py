from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("LAY012",))


def _source(root: Path, source: str, *, config: str = CONFIG) -> None:
    write_project(
        root, {"smelt.yml": config, "app/__init__.py": "", "app/orders.py": source}
    )


@pytest.mark.parametrize(
    ("source", "name", "line"),
    [
        ("# Get user by id\ndef get_user_by_id(user_id): ...\n", "get_user_by_id", 1),
        ("# Returns the users.\ndef users(): ...\n", "users", 1),
        ("# Creates an order\ndef create_order(): ...\n", "create_order", 1),
        ("# Order repository\nclass OrderRepository: ...\n", "OrderRepository", 1),
        ("# The HTTP client\nclass HTTPClient: ...\n", "HTTPClient", 1),
        ("# Max retries\nMAX_RETRIES = 3\n", "MAX_RETRIES", 1),
        ("# Timeout seconds\ntimeout_seconds: int = 5\n", "timeout_seconds", 1),
        (
            "import functools\n\n# Cached entries\n@functools.cache\ndef cached_entries(): ...\n",
            "cached_entries",
            3,
        ),
        (
            "class Orders:\n    # Get the total\n    def get_total(self): ...\n",
            "get_total",
            2,
        ),
        (
            "# Order\n# repository\nclass OrderRepository: ...\n",
            "OrderRepository",
            1,
        ),
    ],
)
def test_comments_that_restate_the_name_are_reported(
    tmp_path: Path, source: str, name: str, line: int
) -> None:
    _source(tmp_path, source)
    [finding] = violations(tmp_path, OPTIONS)
    assert (finding.code, finding.path, finding.line) == (
        "LAY012",
        "app/orders.py",
        line,
    )
    assert finding.expected is not None
    assert (finding.expected["kind"], finding.expected["name"]) == ("comment", name)
    assert finding.hint is not None
    assert f"only repeats the name `{name}`" in finding.hint


@pytest.mark.parametrize(
    ("source", "name"),
    [
        (
            'class OrderRepository:\n    """Repository for orders."""\n',
            "OrderRepository",
        ),
        ('def get_user(user_id):\n    """Get the user."""\n', "get_user"),
        ('def entries():\n    """\n    Returns entries.\n    """\n', "entries"),
        ('"""Orders."""\n', "orders"),
    ],
)
def test_docstrings_that_restate_the_name_are_reported(
    tmp_path: Path, source: str, name: str
) -> None:
    _source(tmp_path, source)
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["kind"] == "docstring"
    assert finding.expected["name"] == name
    assert finding.expected["required_docstring"] is None
    assert finding.hint is not None
    assert "remove the docstring" in finding.hint


@pytest.mark.parametrize(
    "source",
    [
        "# Cached because the upstream API is rate limited\ndef get_user_by_id(user_id): ...\n",
        "# seconds\ntimeout = 5\n",
        "# e.g. get_user(1)\ndef get_user(user_id): ...\n",
        "# See https://example.com/users\ndef users(): ...\n",
        "# noqa: E501\ndef users(): ...\n",
        "# type: ignore\nusers = []\n",
        "# pragma: no cover\ndef users(): ...\n",
        "# smelt: ignore[LAY012] -- kept for the docs\ndef users(): ...\n",
        "# Copyright 2026 Example\n# License: MIT\ndef users(): ...\n",
        "# TODO(#12): users\ndef users(): ...\n",
        "# ---- users ----\ndef users(): ...\n",
        "# Helpers\ndef helper(): ...\n",
        "# users\n\ndef users(): ...\n",
        "def users(): ...  # users\n",
        "# Users who have not confirmed their email\ndef users(): ...\n",
        "x, y = 1, 2\n",
        'def get_user(user_id):\n    """Get the user.\n\n    Raises:\n        LookupError: when the user is unknown.\n    """\n',
        'class Orders:\n    """Orders by customer; archived orders are never returned."""\n',
    ],
)
def test_comments_that_add_information_are_not_reported(
    tmp_path: Path, source: str
) -> None:
    _source(tmp_path, source)
    assert violations(tmp_path, OPTIONS) == []


def test_required_module_docstring_is_reported_with_a_better_docstring_hint(
    tmp_path: Path,
) -> None:
    config = CONFIG + "style: {module_docstrings: require}\n"
    _source(tmp_path, '"""Orders."""\n', config=config)
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.expected is not None
    assert (
        finding.expected["required_docstring"] == "layout/module-docstring-convention"
    )
    assert finding.hint is not None
    assert "instead of removing it" in finding.hint


@pytest.mark.parametrize(
    "source",
    [
        'from typing import Protocol\n\nclass Store(Protocol):\n    def save_order(self, order):\n        """Save the order."""\n',
        'from abc import ABC, abstractmethod\n\nclass Store(ABC):\n    @abstractmethod\n    def save_order(self, order):\n        """Save the order."""\n',
    ],
)
def test_required_interface_docstring_points_to_a_better_docstring(
    tmp_path: Path, source: str
) -> None:
    _source(tmp_path, source)
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.expected is not None
    assert (
        finding.expected["required_docstring"] == "idioms/missing-interface-docstring"
    )


def test_opt_in_and_tests_are_not_reported(tmp_path: Path) -> None:
    source = "# Get user\ndef get_user(): ...\n"
    _source(tmp_path, source)
    write_project(tmp_path, {"tests/test_orders.py": source})
    assert violations(tmp_path) == []
    write_project(
        tmp_path, {"smelt.yml": CONFIG + "rules: {layout/redundant-comment: hint}\n"}
    )
    assert [(v.code, v.path) for v in violations(tmp_path)] == [
        ("LAY012", "app/orders.py")
    ]
