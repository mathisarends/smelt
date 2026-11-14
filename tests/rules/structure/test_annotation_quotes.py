from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

SELECT = CheckOptions(select=("SMT346",))
FUTURE = "from __future__ import annotations\n\n"
ISSUE_EXAMPLE = (
    "from abc import ABC, abstractmethod\n\n\n"
    "class BrowserHandoff(ABC):\n"
    "    @abstractmethod\n"
    '    async def tabs(self, *, user_id: int) -> list["BrowserWorkspaceTab"]:\n'
    '        """List retained tabs."""\n\n\n'
    "class BrowserWorkspaceTab:\n"
    "    pass\n"
)


def _config(*, version: str = "3.12", policy: str | None = None) -> str:
    contracts = (
        f"structure:\n  contracts:\n    headers: {{match: app, future_annotations: {policy}}}\n"
        if policy
        else ""
    )
    return (
        "version: 1\nproject:\n  root_packages: [app]\n"
        f'  python_version: "{version}"\n{contracts}'
    )


def _project(tmp_path: Path, source: str, **config: str) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": _config(**config),
            "app/__init__.py": "",
            "app/handoff.py": source,
        },
    )


def test_suggests_future_import_for_quoted_forward_reference(tmp_path: Path) -> None:
    root = _project(tmp_path, ISSUE_EXAMPLE)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT346",
        "app/handoff.py",
        6,
        51,
    )
    assert finding.message == (
        "1 quoted annotation(s); an annotations future import defers them"
    )
    assert finding.expected is not None
    assert finding.expected["action"] == "add-future-import"
    assert finding.expected["annotations"] == [
        {"line": 6, "text": "BrowserWorkspaceTab"}
    ]
    assert finding.expected["future_annotations"] is None
    assert finding.hint == (
        "Add from __future__ import annotations and remove the quotes; "
        "verify runtime annotation consumers."
    )
    assert (root / "app/handoff.py").read_text() == ISSUE_EXAMPLE


def test_reports_redundant_quotes_with_future_import(tmp_path: Path) -> None:
    source = FUTURE + 'def tabs(owner: "Owner") -> "Tab": ...\n'
    [finding] = violations(_project(tmp_path, source), SELECT)
    assert finding.expected is not None
    assert finding.expected["action"] == "unquote"
    assert [entry["text"] for entry in finding.expected["annotations"]] == [
        "Owner",
        "Tab",
    ]
    assert finding.hint == "Remove the quotes."


@pytest.mark.parametrize("policy", [None, "forbid", "when-needed"])
def test_reports_redundant_quotes_on_python_314(
    tmp_path: Path, policy: str | None
) -> None:
    source = 'def tabs() -> "Tab": ...\n'
    config = {"version": "3.14"} | ({"policy": policy} if policy else {})
    [finding] = violations(_project(tmp_path, source, **config), SELECT)
    assert finding.message == (
        "1 quoted annotation(s); annotations are deferred on Python 3.14+"
    )


@pytest.mark.parametrize(
    "source",
    [
        'value: "Tab | None" = None\n',
        'class Holder:\n    tab: "Tab"\n',
        'def tabs(*tabs: "Tab", **named: "Tab") -> None: ...\n',
        'def tabs() -> dict[str, "Tab"]: ...\n',
        'from typing import Annotated\n\ndef tabs(tab: Annotated["Tab", "meta"]) -> None: ...\n',
    ],
)
def test_reports_quoted_annotation_positions(tmp_path: Path, source: str) -> None:
    [finding] = violations(_project(tmp_path, source), SELECT)
    assert finding.expected is not None
    assert len(finding.expected["annotations"]) >= 1
    assert all(
        entry["text"] in {"Tab", "Tab | None"}
        for entry in finding.expected["annotations"]
    )


@pytest.mark.parametrize(
    "source",
    [
        "def tabs(tab: Tab) -> Tab: ...\n",
        'from typing import Literal\n\ndef mode(value: Literal["a", "b"]) -> None: ...\n',
        'import typing\n\ndef mode(value: typing.Literal["a"]) -> None: ...\n',
        'from typing import Annotated\n\ndef tabs(tab: Annotated[int, "meta"]) -> None: ...\n',
        'from typing import TypeVar, cast\n\nT = TypeVar("T", bound="Tab")\nVALUE = cast("Tab", None)\n',
        'def tabs(default="Tab"): ...\n',
    ],
)
def test_accepts_unquoted_literals_metadata_and_runtime_strings(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), SELECT) == []


def test_forbid_policy_keeps_quotes_before_python_314(tmp_path: Path) -> None:
    source = 'def tabs() -> "Tab": ...\n'
    assert violations(_project(tmp_path, source, policy="forbid"), SELECT) == []


def test_when_needed_policy_suggests_future_import(tmp_path: Path) -> None:
    source = 'def tabs() -> "Tab": ...\n'
    [finding] = violations(_project(tmp_path, source, policy="when-needed"), SELECT)
    assert finding.expected is not None
    assert (finding.expected["action"], finding.expected["future_annotations"]) == (
        "add-future-import",
        "when-needed",
    )


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": _config(),
            "app/__init__.py": "",
            "tests/test_handoff.py": 'def helper() -> "Tab": ...\n',
        },
    )
    assert violations(root, SELECT) == []
