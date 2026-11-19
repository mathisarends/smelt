from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

SELECT = CheckOptions(select=("SMT371",))
CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
LEGACY = """
from typing import Generic, TypeVar

_Result = TypeVar("_Result")

class Event(Generic[_Result]):  # noqa: UP046 -- Preserve explicit invariance.
    pass
"""


@pytest.mark.parametrize("version", ["3.12", "3.14"])
def test_reports_legacy_generic_at_declared_minimum(
    tmp_path: Path, version: str
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG
            + "rules: {typing/prefer-inline-type-parameters: warning}\n",
            "pyproject.toml": f'[project]\nrequires-python = ">={version}"\n',
            "app/events.py": LEGACY,
        },
    )

    [finding] = violations(root)

    assert (finding.code, finding.path, finding.line) == ("SMT371", "app/events.py", 5)
    assert finding.expected is not None
    assert finding.expected["class"] == "Event"
    assert finding.expected["python"]["minimum"] == version


@pytest.mark.parametrize("requires", ['requires-python = ">=3.11"', ""])
def test_accepts_legacy_generic_on_older_or_unknown_target(
    tmp_path: Path, requires: str
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "pyproject.toml": f"[project]\n{requires}\n",
            "app/events.py": LEGACY,
        },
    )

    assert violations(root, SELECT) == []


def test_resolves_generic_imports_and_accepts_inline_parameters(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": 'version: 1\nproject: {root_packages: [app], python_version: "3.12"}\n',
            "pyproject.toml": '[project]\nrequires-python = ">=3.11"\n',
            "app/events.py": """
                import typing_extensions as te
                from typing import Generic as GenericBase

                T = te.TypeVar("T")

                class Legacy(GenericBase[T]): ...
                class Qualified(te.Generic[T]): ...
                class Inline[T]: ...
                class Specialized(Legacy[int]): ...
                class Generic[T]: ...
                class Unrelated[T](Generic[T]): ...
                """,
            "app/shadowed.py": """
                from typing import Generic

                class Custom[T]: ...
                Generic = Custom
                class Unrelated[T](Generic[T]): ...
                """,
        },
    )

    assert [(finding.path, finding.line) for finding in violations(root, SELECT)] == [
        ("app/events.py", 6),
        ("app/events.py", 7),
    ]


def test_accepts_reasoned_suppression_for_invariant_contract(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "pyproject.toml": '[project]\nrequires-python = ">=3.12"\n',
            "app/events.py": LEGACY.replace(
                "# noqa: UP046", "# smelt: ignore[typing/prefer-inline-type-parameters]"
            ),
        },
    )

    assert violations(root, SELECT) == []
