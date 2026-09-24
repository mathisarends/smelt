from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [gw]
architecture:
  features:
    root: gw.features
  shared: [gw.shared]
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
  cross_feature:
    allow: ["application -> application"]
"""


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    base = {
        "smelt.yaml": CONFIG,
        "gw/__init__.py": "",
        "gw/shared/__init__.py": "",
        "gw/features/__init__.py": "",
        "gw/features/voice/__init__.py": "",
        "gw/features/voice/domain/__init__.py": "",
        "gw/features/voice/domain/call.py": "",
        "gw/features/voice/application/__init__.py": "",
        "gw/features/voice/application/calls.py": "",
        "gw/features/billing/__init__.py": "",
        "gw/features/billing/domain/__init__.py": "",
        "gw/features/billing/application/__init__.py": "",
    }
    return write_project(tmp_path, {**base, **files})


class TestCrossFeatureImport:
    def test_allowed_layer_pair(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "gw/features/billing/application/invoices.py": "from gw.features.voice.application import calls\n"
            },
        )

        assert violations(root, CheckOptions(select=("SMT102",))) == []

    def test_disallowed_layer_pair(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "gw/features/billing/application/invoices.py": "from gw.features.voice.domain import call\n"
            },
        )

        [found] = violations(root, CheckOptions(select=("SMT102",)))

        assert (
            found.message
            == "feature billing must not import feature voice (application -> domain)"
        )
        assert found.expected == {"cross_feature_allow": ["application -> application"]}

    def test_namespace_feature_container_inside_regular_package(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "gw/features/billing/application/invoices.py": (
                    "from gw.features.voice.domain import call\n"
                )
            },
        )
        (root / "gw/features/__init__.py").unlink()

        [found] = violations(root, CheckOptions(select=("SMT102",)))

        assert found.path == "gw/features/billing/application/invoices.py"
        assert found.message == (
            "feature billing must not import feature voice (application -> domain)"
        )

    def test_default_allow_disables_the_rule(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace(
                    '    allow: ["application -> application"]', "    default: allow"
                ),
                "gw/features/billing/domain/money.py": "from gw.features.voice.domain import call\n",
            },
        )

        assert violations(root, CheckOptions(select=("SMT102",))) == []


class TestSharedImportsFeature:
    def test_shared_importing_feature(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path, {"gw/shared/clock.py": "import gw.features.voice.domain.call\n"}
        )

        [found] = violations(root, CheckOptions(select=("SMT105",)))

        assert (
            found.message
            == "shared module gw.shared.clock must not import feature voice"
        )

    def test_feature_importing_shared_is_fine(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "gw/shared/clock.py": "",
                "gw/features/voice/domain/time.py": "from gw.shared import clock\n",
            },
        )

        assert violations(root, CheckOptions(select=("SMT1",))) == []
