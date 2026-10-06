from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

ONLY_SMT401 = CheckOptions(select=("SMT401",))

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
    @pytest.mark.parametrize("prefix", ["", "./"])
    @pytest.mark.parametrize("separator", ["/", "\\"])
    def test_workspace_roots_have_canonical_paths(
        self,
        tmp_path: Path,
        prefix: str,
        separator: str,
    ) -> None:
        sources = [
            prefix + p.replace("/", separator)
            for p in ("backend/src", "libs/agent/src")
        ]
        tests = [
            prefix + p.replace("/", separator) + separator
            for p in ("backend/tests", "libs/agent/tests")
        ]
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": (
                    "version: 1\nproject:\n  root_packages: [app, agent]\n"
                    f"  source_roots: {json.dumps(sources)}\n  test_roots: {json.dumps(tests)}\n"
                    "tests: {layout: mirror}\n"
                ),
                "backend/src/app/__init__.py": "",
                "backend/src/app/invoice.py": "",
                "backend/tests/test_invoice.py": "",
                "libs/agent/src/agent/__init__.py": "",
                "libs/agent/src/agent/tool.py": "",
                "libs/agent/tests/test_tool.py": "",
            },
        )

        assert violations(root, ONLY_SMT401) == []

    def test_mirrored_paths_pass_whatever_they_import(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/billing/test_invoice.py": "import os\n",
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
        assert found.expected == {
            "path": "tests/billing/test_invoice.py",
            "source": "app/billing/invoice.py",
        }

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
                "app/billing/payments.py does not exist "
                '(did you mean "payment.py"?)',
            ),
        ]
        assert [v.expected for v in found] == [
            {"source": "app/billing/ghost.py"},
            {"source": "app/billing/payments.py"},
        ]

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

    def test_name_outside_the_pattern(self, tmp_path: Path) -> None:
        root = _mirror(tmp_path, {"tests/billing/invoice_test.py": ""})

        [found] = violations(root, ONLY_SMT401)

        assert found.message == (
            "invoice_test.py does not match tests.mirror ({path}/test_{module}.py)"
        )

    def test_name_outside_the_pattern_with_imports(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {"tests/billing/invoice_test.py": "from app.billing import invoice\n"},
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.message == "invoice_test.py should be named test_invoice.py"
        assert found.expected == {
            "path": "tests/billing/test_invoice.py",
            "source": "app/billing/invoice.py",
        }


class TestMirrorPattern:
    def test_suffix_style(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {"tests/billing/invoice_test.py": "", "tests/billing/billing_test.py": ""},
            '  mirror: "{path}/{module}_test.py"\n',
        )

        assert violations(root, ONLY_SMT401) == []

    def test_with_root_package(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/app/billing/test_invoice.py": "",
                "tests/other/billing/test_invoice.py": "",
            },
            '  mirror: "{root}/{path}/test_{module}.py"\n',
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.path == "tests/other/billing/test_invoice.py"

    def test_prefix_directory(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/unit/billing/test_invoice.py": "",
                "tests/billing/test_payment.py": "from app.billing import payment\n",
            },
            '  mirror: "unit/{path}/test_{module}.py"\n',
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.message == "test_payment.py belongs in tests/unit/billing/"
        assert found.expected == {
            "path": "tests/unit/billing/test_payment.py",
            "source": "app/billing/payment.py",
        }


WORKSPACE_CONFIG = """
    version: 1
    project:
      root_packages: [backend, agent]
      source_roots: [backend/src, libs/agent/src]
      test_roots: [backend/tests, libs/agent/tests]
    tests:
      layout: mirror
"""
WORKSPACE_SOURCES = {
    "backend/src/backend/__init__.py": "",
    "backend/src/backend/users/__init__.py": "",
    "backend/src/backend/users/repository.py": "",
    "libs/agent/src/agent/__init__.py": "",
    "libs/agent/src/agent/tools/__init__.py": "",
    "libs/agent/src/agent/tools/executor.py": "",
}


class TestWorkspace:
    def test_each_test_root_mirrors_its_own_member(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": WORKSPACE_CONFIG,
                **WORKSPACE_SOURCES,
                "backend/tests/users/test_repository.py": "",
                "libs/agent/tests/tools/test_executor.py": "",
            },
        )

        assert violations(root, ONLY_SMT401) == []

    def test_orphan_names_the_source_of_its_member(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": WORKSPACE_CONFIG,
                **WORKSPACE_SOURCES,
                "libs/agent/tests/tools/test_ghost.py": "",
                "backend/tests/tools/test_executor.py": "",
            },
        )

        found = violations(root, ONLY_SMT401)

        assert [v.message for v in found] == [
            "test_executor.py mirrors no source module: "
            "backend/src/backend/tools/executor.py does not exist",
            "test_ghost.py mirrors no source module: "
            "libs/agent/src/agent/tools/ghost.py does not exist",
        ]


class TestSuggestions:
    def test_name_spelling_out_the_package_is_renamed(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/billing/test_billing_invoice.py": (
                    "from app.billing import payment\n"
                    "from app.billing.invoice import Invoice\n"
                )
            },
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.message == (
            "test_billing_invoice.py should be named test_invoice.py"
        )
        assert found.source_module is None  # keeps debt fingerprints stable
        assert found.expected == {
            "path": "tests/billing/test_invoice.py",
            "source": "app/billing/invoice.py",
        }

    def test_name_ending_in_a_neighbouring_module_is_renamed(
        self, tmp_path: Path
    ) -> None:
        root = _mirror(tmp_path, {"tests/billing/test_billing_payment.py": ""})

        [found] = violations(root, ONLY_SMT401)

        assert found.message == (
            "test_billing_payment.py should be named test_payment.py"
        )

    def test_name_suffix_loses_against_imports_of_another_module(
        self, tmp_path: Path
    ) -> None:
        root = _mirror(
            tmp_path,
            {
                # like test_session_presentation_event_mapper.py, which tests
                # presentation/rpc/mappers.py and not presentation/mapper.py
                "app/billing/stripe/charges.py": "",
                "tests/billing/test_billing_stripe_payment.py": (
                    "from app.billing.stripe.charges import charge\n"
                ),
            },
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.message.startswith(
            "test_billing_stripe_payment.py mirrors no source module"
        )

    def test_never_suggests_a_path_another_test_holds(self, tmp_path: Path) -> None:
        root = _mirror(
            tmp_path,
            {
                "tests/billing/test_payment.py": "",
                "tests/billing/test_billing_payment.py": "",
            },
        )

        [found] = violations(root, ONLY_SMT401)

        assert found.path == "tests/billing/test_billing_payment.py"
        assert found.message.startswith("test_billing_payment.py mirrors no source")
        assert found.hint is not None
        assert found.hint.endswith(
            "tests/billing/test_payment.py already exists; merge the two tests."
        )

    def test_typo_suggests_the_neighbouring_module(self, tmp_path: Path) -> None:
        root = _mirror(tmp_path, {"tests/billing/test_invoise.py": ""})

        [found] = violations(root, ONLY_SMT401)

        assert found.message.endswith('(did you mean "invoice.py"?)')

    def test_missing_root_placeholder_is_pointed_out(self, tmp_path: Path) -> None:
        root = _mirror(tmp_path, {"tests/app/billing/test_invoice.py": ""})

        [found] = violations(root, ONLY_SMT401)

        assert found.hint is not None
        assert found.hint.endswith(
            'The test path starts with the root package "app"; if all tests do, '
            'set tests.mirror to "{root}/{path}/test_{module}.py".'
        )
