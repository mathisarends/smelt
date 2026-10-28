from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("from app.service import _helper", True),
        ("from app.service import _helper as public", True),
        ("import app._internal", True),
        ("from app import _internal", True),
        ("from app import service as s\ndef test_run(): s._helper()", True),
        ("from app.service import public", False),
        ("from app.service import __version__", False),
        ("from unittest.mock import _ANY", False),
        ("def _helper(): pass\ndef test_run(): _helper()", False),
        ("from app import service as s\ndef test_run(s): s._helper()", False),
        (
            "from app import service as s\ndef test_run():\n    s = fake\n    s._helper()",
            False,
        ),
    ],
)
def test_private_production_access(tmp_path: Path, source: str, flagged: bool) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {forbid_private_imports: true}",
            "app/__init__.py": "",
            "app/service.py": "def _helper(): pass\ndef public(): pass",
            "app/_internal.py": "",
            "tests/test_service.py": source,
        },
    )
    found = violations(tmp_path, CheckOptions(select=("SMT420",)))
    assert bool(found) is flagged
    if found:
        assert found[0].expected is not None
        assert found[0].expected["private_target"].startswith("app.")
