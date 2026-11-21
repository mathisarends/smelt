from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
POLICY = "rules: {complexity/inline-constructor-receiver: warning}\n"
SELECT = CheckOptions(select=("CPX008",))
POINTER_TOOLS = """\
from plugins import PointerTools, Param

pointer = PointerTools().tool(
    PointerTools.drag_at,  # typed ToolReference, must belong to this plugin
    description="Press, move, release. Also for swipes and carousels.",
    parameters={
        "duration_seconds": Param(
            default=0.15,
            le=1.0,
            description="Use ~0.15s for swipes, longer for precise drags.",
        ),
    },
)
"""


def _project(tmp_path: Path, source: str, *, policy: str = "") -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + policy,
            "app/__init__.py": "",
            "app/pointer.py": source,
        },
    )


def test_inline_pointer_tools_receiver_is_opt_in(tmp_path: Path) -> None:
    root = _project(tmp_path, POINTER_TOOLS)
    assert violations(root) == []

    (root / "smelt.yml").write_text(CONFIG + POLICY, encoding="utf-8")
    [finding] = violations(root)

    assert (finding.code, finding.severity, finding.path) == (
        "CPX008",
        Severity.WARNING,
        "app/pointer.py",
    )
    assert (finding.line, finding.column, finding.end_column) == (3, 11, 25)
    assert finding.expected == {"constructor": "PointerTools", "method": "tool"}


@pytest.mark.parametrize(
    "source",
    [
        POINTER_TOOLS.replace(
            "pointer = PointerTools().tool(",
            "pointer_tools = PointerTools()\npointer = pointer_tools.tool(",
        ),
        "from plugins import make_pointer\npointer = make_pointer().tool()\n",
        "from plugins import PointerTools\npointers = [PointerTools().tool() for _ in range(2)]\n",
        "from plugins import PointerTools\npointer = PointerTools().tool().configure()\n",
        "from plugins import PointerTools\ndef run(PointerTools):\n    PointerTools().tool()\n",
        "def PointerTools():\n    pass\npointer = PointerTools().tool()\n",
        "from plugins import *\nfrom toolkit import PointerTools\npointer = PointerTools().tool()\n",
        "pointer = PointerTools().tool()\n",
    ],
)
def test_named_receivers_and_out_of_scope_shapes_pass(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), SELECT) == []


def test_local_class_and_import_aliases_are_recognized(tmp_path: Path) -> None:
    source = """\
import plugins as sdk
from .tools import PointerTools as pointer_class

class LocalPointer:
    pass

async def run():
    tool: object = await LocalPointer().tool()
    sdk.PointerTools().reset()
    return pointer_class().tool()
"""
    findings = violations(_project(tmp_path, source), SELECT)
    assert [(finding.line, finding.expected) for finding in findings] == [
        (8, {"constructor": "LocalPointer", "method": "tool"}),
        (9, {"constructor": "sdk.PointerTools", "method": "reset"}),
        (10, {"constructor": "pointer_class", "method": "tool"}),
    ]
