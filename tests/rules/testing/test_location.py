from typing import TYPE_CHECKING

from smelt.diagnostics.violation import FileMove
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

ONLY_SMT401 = CheckOptions(select=("SMT401",))

FEATURE_CONFIG = """
version: 1
project:
  root_packages: [gw]
architecture:
  features:
    root: gw.features
  layers:
    domain: {path: domain}
tests:
  layout: feature
  pattern: "tests/{feature}"
"""

SOURCES = {
    "gw/__init__.py": "",
    "gw/features/__init__.py": "",
    "gw/features/voice/__init__.py": "",
    "gw/features/voice/domain/__init__.py": "",
    "gw/features/voice/domain/calls.py": "",
    "gw/features/billing/__init__.py": "",
    "gw/features/billing/domain/__init__.py": "",
    "gw/features/billing/domain/money.py": "",
}


class TestFeatureLayout:
    def test_test_in_feature_directory_is_fine(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "tests/voice/test_calls.py": "from gw.features.voice.domain import calls\n",
                "tests/conftest.py": "from gw.features.voice.domain import calls\n",
            },
        )

        assert violations(root, ONLY_SMT401) == []

    def test_misplaced_test_gets_move_fix(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "tests/test_calls.py": "from gw.features.voice.domain import calls\n",
            },
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.message == "test_calls.py belongs in tests/voice/"
        assert found.fix is not None
        assert found.fix.edits == (
            FileMove("tests/test_calls.py", "tests/voice/test_calls.py"),
        )

    def test_test_spanning_features_may_live_in_either(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "tests/billing/test_charge.py": (
                    "from gw.features.voice.domain import calls\n"
                    "from gw.features.billing.domain import money\n"
                ),
            },
        )

        assert violations(root, ONLY_SMT401) == []

    def test_test_without_feature_imports_is_ignored(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "tests/test_misc.py": "import os\n",
            },
        )

        assert violations(root, ONLY_SMT401) == []


class TestMirrorLayout:
    def test_expected_path_mirrors_the_module(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": (
                    "version: 1\nproject:\n  root_packages: [app]\n"
                    "tests:\n  layout: mirror\n"
                ),
                "app/__init__.py": "",
                "app/billing/__init__.py": "",
                "app/billing/invoices.py": "",
                "tests/billing/test_invoices.py": "from app.billing import invoices\n",
                "tests/test_invoices_e2e.py": "from app.billing import invoices\n",
                "tests/test_invoices.py": "from app.billing.invoices import total\n",
            },
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.path == "tests/test_invoices.py"
        assert found.expected == {"path": "tests/billing/test_invoices.py"}
        assert found.fix is None
