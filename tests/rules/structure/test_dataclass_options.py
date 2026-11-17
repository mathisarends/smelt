from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

POLICY = """\
version: 1
project: {root_packages: [app]}
structure:
  contracts:
    records:
      match: app
      dataclass_options: [frozen, slots]
"""
SELECT = CheckOptions(select=("SMT351",))
HEADER = "from dataclasses import dataclass\n\n\n"


def _project(
    tmp_path: Path, source: str, *, extra: dict[str, str] | None = None
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": POLICY,
            "app/__init__.py": "",
            "app/records.py": HEADER + source,
            **(extra or {}),
        },
    )


def _missing(root: Path) -> list[tuple[str, list[str]]]:
    return [
        (item.expected["class"], item.expected["missing"])
        for item in violations(root)
        if item.expected is not None
    ]


def test_reports_missing_options_from_the_issue(tmp_path: Path) -> None:
    source = (
        "@dataclass(frozen=True)\nclass Hooks:\n    name: str\n\n\n"
        "@dataclass\nclass Policy:\n    name: str\n"
    )
    root = _project(tmp_path, source)

    first, second = violations(root)

    assert (first.code, first.path, first.line, first.column) == (
        "SMT351",
        "app/records.py",
        5,
        1,
    )
    assert first.expected == {
        "contract": "records",
        "class": "Hooks",
        "dataclass_options": ["frozen", "slots"],
        "missing": ["slots"],
        "automatic_rewrite": False,
    }
    assert second.expected is not None
    assert second.expected["missing"] == ["frozen", "slots"]


@pytest.mark.parametrize(
    "decorator",
    [
        "@dataclass(frozen=True, slots=True)",
        "@dataclasses.dataclass(slots=True, frozen=True, eq=False)",
        "@dataclass(frozen=FROZEN, slots=True)",
    ],
    ids=["exact", "qualified", "non-literal"],
)
def test_accepts_dataclass_with_required_options(
    tmp_path: Path, decorator: str
) -> None:
    source = (
        "import dataclasses\n\nFROZEN = True\n\n\n"
        f"{decorator}\nclass Policy:\n    name: str\n"
    )

    assert _missing(_project(tmp_path, source)) == []


def test_reports_explicitly_disabled_option(tmp_path: Path) -> None:
    source = "@dataclass(frozen=False, slots=True)\nclass Policy:\n    name: str\n"

    assert _missing(_project(tmp_path, source)) == [("Policy", ["frozen"])]


def test_ignores_plain_classes_and_tests(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "class Policy:\n    name: str\n",
        extra={
            "tests/test_records.py": HEADER + "@dataclass\nclass Case:\n    name: str\n"
        },
    )

    assert _missing(root) == []


def test_skips_slots_below_bases_from_outside_the_project(tmp_path: Path) -> None:
    source = (
        "import abc\n"
        "from typing import Generic, TypeVar\n\n"
        "import pydantic\n"
        "from vendor.events import Event\n\n"
        "from app.base import Record\n\n"
        "T = TypeVar('T')\n\n\n"
        "@dataclass(frozen=True)\nclass Changed(Event[None]):\n    tab: str\n\n\n"
        "@dataclass(frozen=True)\nclass Model(pydantic.BaseModel):\n    tab: str\n\n\n"
        "@dataclass(frozen=True)\nclass Failure(Exception):\n    tab: str\n\n\n"
        "@dataclass(frozen=True)\nclass Box(Generic[T]):\n    item: T\n\n\n"
        "@dataclass(frozen=True)\nclass Port(abc.ABC):\n    tab: str\n\n\n"
        "@dataclass(frozen=True)\nclass Child(Record):\n    tab: str\n\n\n"
        "@dataclass\nclass Mutable(Event[None]):\n    tab: str\n"
    )
    root = _project(
        tmp_path,
        source,
        extra={
            "app/base.py": HEADER
            + "@dataclass(frozen=True, slots=True)\nclass Record:\n    id: str\n"
        },
    )

    assert _missing(root) == [
        ("Box", ["slots"]),
        ("Port", ["slots"]),
        ("Child", ["slots"]),
        ("Mutable", ["frozen"]),
    ]


def test_skips_slots_for_cached_properties(tmp_path: Path) -> None:
    source = (
        "import functools\n\n\n"
        "@dataclass(frozen=True)\nclass Index:\n    paths: tuple[str, ...]\n\n"
        "    @functools.cached_property\n"
        "    def by_path(self) -> dict[str, int]:\n"
        "        return {path: index for index, path in enumerate(self.paths)}\n"
    )

    assert _missing(_project(tmp_path, source)) == []


def test_closest_package_policy_wins(tmp_path: Path) -> None:
    config = POLICY + (
        "    state:\n"
        "      match: app.state\n"
        "      dataclass_options: [slots]\n"
        "    legacy:\n"
        "      match: app.legacy\n"
        "      dataclass_options: []\n"
    )
    source = HEADER + "@dataclass\nclass Holder:\n    name: str\n"
    root = write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/state/__init__.py": "",
            "app/state/tabs.py": source,
            "app/legacy/__init__.py": "",
            "app/legacy/old.py": source,
        },
    )

    assert [
        (item.path, item.expected and item.expected["missing"])
        for item in violations(root, SELECT)
    ] == [("app/state/tabs.py", ["slots"])]


def test_requires_kw_only_when_listed(tmp_path: Path) -> None:
    config = POLICY.replace("[frozen, slots]", "[kw_only]")
    source = HEADER + "@dataclass\nclass Policy:\n    name: str\n"
    root = write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/records.py": source},
    )

    assert _missing(root) == [("Policy", ["kw_only"])]


def test_is_inactive_without_policy(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/records.py": HEADER + "@dataclass\nclass Policy:\n    name: str\n",
        },
    )

    assert violations(root) == []


def test_rules_entry_can_turn_it_off(tmp_path: Path) -> None:
    config = POLICY + "rules: {modeling/missing-dataclass-option: off}\n"
    root = write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/records.py": HEADER + "@dataclass\nclass Policy:\n    name: str\n",
        },
    )

    assert violations(root) == []
