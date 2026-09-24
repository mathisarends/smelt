from __future__ import annotations

from typing import TYPE_CHECKING

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

    def test_misplaced_test_names_the_feature_dir(self, tmp_path: Path) -> None:
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
        assert found.expected == {"path": "tests/voice/test_calls.py"}

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


MIRROR_SOURCES = {
    "app/__init__.py": "",
    "app/billing/__init__.py": "",
    "app/billing/invoice.py": "",
    "app/billing/payment.py": "",
    "app/billing/stripe/__init__.py": "",
}


def _mirror(tmp_path: Path, tests: dict[str, str], options: str = "") -> Path:
    config = (
        "version: 1\nproject:\n  root_packages: [app]\n"
        f"tests:\n  layout: mirror\n{options}"
    )
    return write_project(tmp_path, {"smelt.yaml": config, **MIRROR_SOURCES, **tests})


class TestMirrorLayout:
    def test_mirrored_paths_pass_whatever_they_import(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/billing/test_invoice.py": "import os\n",
                "tests/billing/payment_test.py": "",
                "tests/billing/test_billing.py": "",
                "tests/billing/stripe/test_stripe.py": "",
                "tests/test_app.py": "",
                "tests/billing/conftest.py": "",
                "tests/billing/helpers.py": "",
            },
        )

        assert violations(root, ONLY_SMT401) == []

    def test_imports_name_the_mirrored_location(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {"tests/test_invoice.py": "from app.billing.invoice import total\n"},
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.message == "test_invoice.py belongs in tests/billing/"
        assert found.expected == {"path": "tests/billing/test_invoice.py"}

    def test_orphaned_test_is_reported(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/billing/test_ghost.py": "",
                "tests/billing/test_payments.py": "from app.billing import payment\n",
            },
        )

        found = violations(root, ONLY_SMT401)

        assert [(v.path, v.message) for v in found] == [
            (
                "tests/billing/test_ghost.py",
                "test_ghost.py mirrors no source module: "
                "app/billing/ghost.py does not exist",
            ),
            (
                "tests/billing/test_payments.py",
                "test_payments.py mirrors no source module: "
                "app/billing/payments.py does not exist",
            ),
        ]
        assert all(v.expected is None for v in found)

    def test_package_test_must_live_in_the_package_dir(self, tmp_path: Path) -> None:
        root = _mirror(tmp_path, {"tests/test_billing.py": ""})

        [found] = violations(root, ONLY_SMT401)

        assert found.path == "tests/test_billing.py"

    def test_package_is_not_a_module(self, tmp_path: Path) -> None:
        root = _mirror(tmp_path, {"tests/billing/test_stripe.py": ""})

        [found] = violations(root, ONLY_SMT401)

        assert found.path == "tests/billing/test_stripe.py"

    def test_suffixes_are_off_by_default(self, tmp_path: Path) -> None:
        root = _mirror(tmp_path, {"tests/billing/test_invoice_rounding.py": ""})

        [found] = violations(root, ONLY_SMT401)

        assert found.path == "tests/billing/test_invoice_rounding.py"

    def test_suffixes_when_enabled(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/billing/test_invoice_rounding.py": "",
                "tests/billing/test_invoice_tax_rules.py": "",
                "tests/billing/test_ghost_rounding.py": "",
            },
            "  mirror_suffixes: true\n",
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.path == "tests/billing/test_ghost_rounding.py"

    def test_unmirrored_paths_are_skipped(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/integration/test_checkout.py": "",
                "tests/e2e/flows/test_signup.py": "",
            },
            '  unmirrored: ["tests/integration/**", "tests/e2e"]\n',
        )

        assert violations(root, ONLY_SMT401) == []
