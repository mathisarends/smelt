from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

ONLY_TST012 = CheckOptions(select=("TST012",))
BEHAVIOUR = "def total(items):\n    return sum(items)\n"


def _project(
    tmp_path: Path, files: dict[str, str], *, tests: str = "{layout: mirror}"
) -> Path:
    config = f"version: 2\nproject:\n  root_packages: [app]\ntests: {tests}\n"
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/billing/__init__.py": "",
            "tests/conftest.py": "",
            **files,
        },
    )


def test_module_with_behaviour_and_no_mirrored_test_is_reported(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, {"app/billing/invoice.py": BEHAVIOUR})

    [found] = violations(root, ONLY_TST012)

    assert (found.code, found.path, found.line) == (
        "TST012",
        "app/billing/invoice.py",
        1,
    )
    assert found.expected == {"test_path": "tests/billing/test_invoice.py"}


def test_mirrored_test_covers_the_module(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            "app/billing/invoice.py": BEHAVIOUR,
            "tests/billing/test_invoice.py": "def test_total():\n    assert True\n",
        },
    )

    assert violations(root, ONLY_TST012) == []


def test_custom_mirror_pattern_decides_the_test_path(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {"app/billing/invoice.py": BEHAVIOUR},
        tests="{layout: mirror, mirror: 'unit/{path}/{module}_test.py'}",
    )

    [found] = violations(root, ONLY_TST012)

    assert found.expected == {"test_path": "tests/unit/billing/invoice_test.py"}


@pytest.mark.parametrize(("suffixes", "reported"), [("true", False), ("false", True)])
def test_topic_test_covers_the_module_only_with_mirror_suffixes(
    tmp_path: Path, suffixes: str, reported: bool
) -> None:
    root = _project(
        tmp_path,
        {
            "app/billing/invoice.py": BEHAVIOUR,
            "tests/billing/test_invoice_rounding.py": "",
        },
        tests=f"{{layout: mirror, mirror_suffixes: {suffixes}}}",
    )

    assert bool(violations(root, ONLY_TST012)) is reported


@pytest.mark.parametrize(
    "source",
    [
        "LIMIT = 3\n",
        "from dataclasses import dataclass\n\n@dataclass\nclass Invoice:\n    total: int\n",
        "from typing import Protocol\n\nclass Store(Protocol):\n"
        '    def save(self) -> None:\n        """Persist."""\n        ...\n',
        "class Store:\n    def save(self) -> None:\n        raise NotImplementedError\n",
    ],
    ids=["constants", "data", "protocol", "abstract-stub"],
)
def test_module_without_behaviour_is_not_reported(tmp_path: Path, source: str) -> None:
    root = _project(tmp_path, {"app/billing/invoice.py": source})

    assert violations(root, ONLY_TST012) == []


@pytest.mark.parametrize(
    "path", ["app/billing/__init__.py", "app/__main__.py"], ids=["package", "main"]
)
def test_packages_and_entry_points_are_not_reported(tmp_path: Path, path: str) -> None:
    root = _project(tmp_path, {path: BEHAVIOUR})

    assert violations(root, ONLY_TST012) == []


def test_projects_without_mirror_layout_are_not_reported(tmp_path: Path) -> None:
    root = _project(
        tmp_path, {"app/billing/invoice.py": BEHAVIOUR}, tests="{layout: none}"
    )

    assert violations(root, ONLY_TST012) == []


def test_file_suppression_with_a_reason_keeps_a_deliberately_covered_module(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {
            "app/billing/invoice.py": (
                "# smelt: ignore-file[TST012] -- covered by tests/e2e/test_checkout.py\n"
                + BEHAVIOUR
            )
        },
    )

    assert violations(root, ONLY_TST012) == []
