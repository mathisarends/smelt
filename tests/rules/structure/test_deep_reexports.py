from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

_CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
_ENABLED = _CONFIG + "rules: {visibility/deep-package-reexport: warning}\n"
_RUNTIME = {
    "app/runtime/__init__.py": "",
    "app/runtime/hooks.py": "class ExecuteTool: pass\nclass Next: pass\nclass Helper: pass\n",
}


@pytest.mark.parametrize(
    "initializer",
    [
        "from .runtime.hooks import (\n"
        "    ExecuteTool, Next as Proceed, Helper,\n"
        ")\n"
        "__all__ = ['ExecuteTool', 'Proceed']\n",
        "from app.runtime.hooks import ExecuteTool as ExecuteTool, Next as Proceed, Helper as _Helper\n",
    ],
)
def test_batches_public_names_bypassing_an_initializer(
    tmp_path: Path, initializer: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG,
            "app/__init__.py": initializer,
            **_RUNTIME,
        },
    )

    assert violations(tmp_path) == []
    found = violations(tmp_path, CheckOptions(select=("VIS011",)))
    assert codes_at(found) == [("VIS011", "app/__init__.py", 1)]
    finding = found[0]
    assert finding.expected == {
        "package": "app.runtime",
        "names": ["ExecuteTool", "Next"],
        "reexport_edges": [
            "app.runtime.hooks.ExecuteTool as ExecuteTool",
            "app.runtime.hooks.Next as Proceed",
        ],
    }
    assert finding.target_module == "app.runtime.hooks"
    assert finding.fix is None


def test_each_initializer_exposes_its_immediate_children(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": _ENABLED,
            "app/__init__.py": "from .runtime.tools.hooks import ExecuteTool\n",
            "app/runtime/__init__.py": "from .tools.hooks import ExecuteTool\n",
            "app/runtime/tools/__init__.py": "from .hooks import ExecuteTool, Next\n",
            "app/runtime/tools/hooks.py": "class ExecuteTool: pass\nclass Next: pass\n",
            "app/client.py": "from .runtime.tools.hooks import ExecuteTool\n",
        },
    )

    found = violations(tmp_path)
    assert codes_at(found) == [
        ("VIS011", "app/__init__.py", 1),
        ("VIS011", "app/runtime/__init__.py", 1),
    ]
    assert [v.expected and v.expected["package"] for v in found] == [
        "app.runtime",
        "app.runtime.tools",
    ]
    write_project(
        tmp_path,
        {
            "app/__init__.py": "from .runtime import ExecuteTool\n",
            "app/runtime/__init__.py": "from .tools import ExecuteTool\n",
        },
    )
    assert violations(tmp_path) == []


@pytest.mark.parametrize(
    "initializer",
    [
        "from .runtime.hooks import ExecuteTool as _ExecuteTool\n",
        "from .runtime.hooks import ExecuteTool\n__all__ = []\n",
        "from .runtime.hooks import ExecuteTool\n__all__ = compute_exports()\n",
        "from .runtime.hooks import ExecuteTool\nfrom .exports import __all__\n",
        "from .runtime.hooks import ExecuteTool\n__all__ = ['ExecuteTool']\nfrom .exports import __all__\n",
        "from .runtime.hooks import ExecuteTool\nExecuteTool = custom_tool\n",
        "if enabled:\n    from .runtime.hooks import ExecuteTool\n",
        "from .runtime.hooks import *\n",
    ],
)
def test_skips_unproven_or_internal_exports(tmp_path: Path, initializer: str) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": _ENABLED,
            "app/__init__.py": initializer,
            **_RUNTIME,
        },
    )

    assert violations(tmp_path) == []


def test_leaves_namespace_packages_and_siblings_alone(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": _ENABLED,
            "app/__init__.py": "from .plugins.hooks import Plugin\n",
            "app/plugins/hooks.py": "class Plugin: pass\n",
            "app/other/__init__.py": "from ..runtime.hooks import ExecuteTool\n",
            **_RUNTIME,
        },
    )

    assert violations(tmp_path) == []
