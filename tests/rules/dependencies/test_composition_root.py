from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
architecture:
  composition_root: [app.bootstrap]
  di_frameworks: [dishka]
  layers:
    application:
      path: application
      third_party: {default: allow, deny: [dishka]}
"""


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    base = {
        "smelt.yaml": CONFIG,
        "app/__init__.py": "",
        "app/application/__init__.py": "",
        "app/bootstrap.py": "import dishka\nfrom app.application import service\n",
    }
    return write_project(tmp_path, {**base, **files})


class TestCompositionRootLeak:
    def test_feature_wiring_keeps_its_layer_and_can_import_across_layers(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": """
                    version: 1
                    project: {root_packages: [app]}
                    architecture:
                      features: {root: app.features}
                      composition_root: [app.bootstrap]
                      wiring: [app.features.*.infrastructure.di]
                      di_frameworks: [dishka]
                      layers:
                        domain: {path: domain}
                        application: {path: application, may_depend_on: [domain]}
                        infrastructure: {path: infrastructure, may_depend_on: [application, domain]}
                        presentation: {path: presentation, may_depend_on: [application]}
                """,
                "app/__init__.py": "",
                "app/bootstrap.py": "from app.features.auth.infrastructure import di\n",
                "app/features/auth/__init__.py": "",
                "app/features/auth/application/__init__.py": "",
                "app/features/auth/application/service.py": "",
                "app/features/auth/infrastructure/__init__.py": "",
                "app/features/auth/infrastructure/di.py": (
                    "import dishka\n"
                    "from app.features.auth.presentation import router\n"
                    "from app.features.user.domain import account\n"
                ),
                "app/features/auth/presentation/__init__.py": "",
                "app/features/auth/presentation/router.py": "",
                "app/features/user/__init__.py": "",
                "app/features/user/domain/__init__.py": "",
                "app/features/user/domain/account.py": "",
            },
        )

        outcome = check(root, CheckOptions(select=("SMT101", "SMT102", "SMT106")))

        assert outcome.report.violations == []
        info = outcome.context.model.info("app.features.auth.infrastructure.di")
        assert info is not None
        assert (info.feature, info.layer, info.wiring) == (
            "auth",
            "infrastructure",
            True,
        )

    def test_regular_module_must_not_import_feature_wiring(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": """
                    version: 1
                    project: {root_packages: [app]}
                    architecture:
                      features: {root: app.features}
                      composition_root: [app.bootstrap]
                      wiring: [app.features.auth.infrastructure.di]
                      layers:
                        application: {path: application}
                        infrastructure: {path: infrastructure}
                """,
                "app/__init__.py": "",
                "app/bootstrap.py": "",
                "app/features/auth/__init__.py": "",
                "app/features/auth/application/__init__.py": "",
                "app/features/auth/application/service.py": (
                    "from app.features.auth.infrastructure import di\n"
                ),
                "app/features/auth/infrastructure/__init__.py": "",
                "app/features/auth/infrastructure/di.py": "",
            },
        )

        found = violations(root, CheckOptions(select=("SMT106",)))

        assert [v.message for v in found] == [
            "app.features.auth.application.service must not import the wiring module "
            "app.features.auth.infrastructure.di"
        ]

    def test_provider_package_may_export_its_wiring(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace(
                    "  composition_root: [app.bootstrap]",
                    "  composition_root: [app.bootstrap]\n"
                    "  wiring: [app.providers.database]",
                ),
                "app/providers/__init__.py": "from app.providers.database import Provider\n",
                "app/providers/database.py": "class Provider: pass\n",
            },
        )

        assert violations(root, CheckOptions(select=("SMT106",))) == []

    def test_framework_integration_markers_are_not_container_access(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/service.py": (
                    "from dishka.integrations.fastapi import FromDishka\n"
                ),
            },
        )

        assert violations(root, CheckOptions(select=("SMT106",))) == []

    def test_framework_inside_composition_root_is_fine(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"app/application/service.py": ""})

        assert violations(root, CheckOptions(select=("SMT1",))) == []

    def test_framework_outside_root_wins_over_third_party_rule(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path, {"app/application/service.py": "from dishka import FromDishka\n"}
        )

        [found] = violations(root, CheckOptions(select=("SMT1",)))

        assert found.code == "SMT106"
        assert (
            found.message
            == "dishka may only be used in the composition root (app.bootstrap)"
        )

    def test_importing_the_composition_root(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path, {"app/application/service.py": "from app import bootstrap\n"}
        )

        found = violations(root, CheckOptions(select=("SMT106",)))

        assert [v.message for v in found] == [
            "app.application.service must not import the composition root app.bootstrap"
        ]
