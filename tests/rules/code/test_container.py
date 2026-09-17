from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
architecture:
  composition_root: [app.bootstrap]
"""

ONLY_SMT205 = CheckOptions(select=("SMT205",))


class TestContainerUsage:
    def test_resolution_outside_composition_root(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": CONFIG,
                "app/__init__.py": "",
                "app/bootstrap.py": "sessions = container.get(Sessions)\n",
                "app/routes.py": (
                    "async def start(request) -> None:\n"
                    "    sessions = await request.app.state.container.get(Sessions)\n"
                    "    settings = config.get('KEY')\n"
                    "    value = cache.get(Sessions)\n"
                ),
            },
        )

        found = violations(root, ONLY_SMT205)

        assert codes_at(found) == [("SMT205", "app/routes.py", 2)]
        assert found[0].message == (
            "container.get(...) resolves a dependency outside the composition root "
            "(app.bootstrap)"
        )

    def test_inactive_without_composition_root(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": "",
                "app/routes.py": "x = container.get(Sessions)\n",
            },
        )

        assert violations(root, ONLY_SMT205) == []
