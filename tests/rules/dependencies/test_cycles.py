from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

ONLY_SMT104 = CheckOptions(select=("SMT104",))


class TestSiblingCycles:
    def test_reports_shortest_cycle_once(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": "",
                "app/a.py": "from app import b\n",
                "app/b.py": "from app import c\n",
                "app/c.py": "from app import a\n",
            },
        )

        [found] = violations(root, ONLY_SMT104)

        assert found.message == "import cycle between modules in app: a -> b -> c -> a"
        assert [link.line for link in found.import_chain] == [1, 1, 1]
        assert found.path == "app/a.py"

    def test_import_of_parent_package_is_not_a_cycle(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": "from app.sub import thing\n",
                "app/sub/__init__.py": "",
                "app/sub/thing.py": "import app\n",
            },
        )

        assert violations(root, ONLY_SMT104) == []

    def test_scope_can_be_disabled(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": (
                    "version: 1\nproject:\n  root_packages: [app]\n"
                    "architecture:\n  imports:\n    cycles: [features]\n"
                ),
                "app/__init__.py": "",
                "app/a.py": "from app import b\n",
                "app/b.py": "from app import a\n",
            },
        )

        assert violations(root, ONLY_SMT104) == []


class TestLayerCycles:
    def test_only_cycles_of_allowed_dependencies_are_reported(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": """
                    version: 1
                    project:
                      root_packages: [app]
                    architecture:
                      layers:
                        a: {path: a, may_depend_on: [b]}
                        b: {path: b, may_depend_on: [a]}
                        c: {path: c}
                """,
                "app/__init__.py": "",
                "app/a/__init__.py": "from app.b import x\n",
                "app/b/__init__.py": "",
                "app/b/x.py": "import app.a\n",
                "app/c/__init__.py": "import app.a\n",
            },
        )

        [found] = violations(root, ONLY_SMT104)

        assert found.message == "import cycle between layers: a -> b -> a"


class TestFeatureCycles:
    def test_wiring_import_does_not_create_feature_cycle(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": """
                    version: 1
                    project: {root_packages: [app]}
                    architecture:
                      features: {root: app.features}
                      wiring: [app.features.b.infrastructure.di]
                      layers:
                        application: {path: application}
                        infrastructure: {path: infrastructure}
                      cross_feature:
                        allow: ["application -> application"]
                """,
                "app/__init__.py": "",
                "app/features/a/application/use_case.py": (
                    "from app.features.b.application import port\n"
                ),
                "app/features/b/application/port.py": "",
                "app/features/b/infrastructure/di.py": (
                    "from app.features.a.application import use_case\n"
                ),
            },
        )

        assert violations(root, ONLY_SMT104) == []
