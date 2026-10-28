from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.errors import AnalysisError
from smelt.config import ConfigError
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.render.text import render_text
from smelt.engine.check import CheckOptions, InvalidOptionError
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
  source_roots: [src]
  coverage: strict
architecture:
  layers:
    domain: {path: domain}
"""


def _project(root: Path, files: dict[str, str]) -> Path:
    return write_project(
        root,
        {
            "smelt.yaml": CONFIG,
            "src/app/__init__.py": "",
            "src/app/domain/model.py": "VALUE = 1\n",
            **files,
        },
    )


class TestStrictCoverage:
    def test_loose_script_gets_a_valid_package_placement_hint(
        self, tmp_path: Path
    ) -> None:
        root = _project(tmp_path, {"src/runner.py": "VALUE = 1\n"})

        [found] = violations(root)

        assert found.code == "SMT306"
        assert found.hint is not None
        assert "standalone module into a governed package" in found.hint
        assert found.expected is not None
        assert found.expected["root_package"] is None

    def test_flat_library_with_no_workspace_or_ddd_layout(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject: {root_packages: [arithmetic], coverage: strict}\narchitecture:\n  shared: [arithmetic]\n",
                "arithmetic/__init__.py": "from arithmetic.operations import add\n",
                "arithmetic/operations.py": "def add(left, right):\n    return left + right\n",
                "tests/test_operations.py": "def test_add(): pass\n",
            },
        )

        outcome = check(root, CheckOptions(show_coverage=True))

        assert not outcome.report.failed
        assert outcome.report.coverage is not None
        assert outcome.report.coverage["complete"]
        assert {entry["status"] for entry in outcome.report.coverage["inventory"]} == {
            "governed"
        }

    def test_custom_layers_and_source_roots_do_not_depend_on_uv(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": """
                version: 1
                project:
                  root_packages: [corelib, persistence]
                  source_roots: [python/core, extensions]
                  coverage: strict
                architecture:
                  layers:
                    pure: {path: logic}
                    connectors: {path: drivers, may_depend_on: [pure]}
                  modules: {persistence: connectors}
            """,
                "python/core/corelib/__init__.py": "",
                "python/core/corelib/logic/calculate.py": "VALUE = 1\n",
                "extensions/persistence/__init__.py": "",
                "extensions/persistence/adapter.py": "from corelib.logic.calculate import VALUE\n",
            },
        )

        outcome = check(root, CheckOptions(show_coverage=True))

        assert not outcome.report.failed
        assert outcome.report.coverage is not None
        assert outcome.report.coverage["complete"]
        entries = {
            entry["module"]: entry for entry in outcome.report.coverage["inventory"]
        }
        assert entries["corelib.logic.calculate"]["layer"] == "pure"
        assert entries["persistence.adapter"]["layer"] == "connectors"
        assert entries["persistence.adapter"]["source_root"] == "extensions"

    @pytest.mark.parametrize(
        "metadata", ["[invalid", '[tool.uv.workspace]\nmembers = "libs/*"\n']
    )
    def test_strict_discovery_does_not_hide_invalid_workspace_metadata(
        self, tmp_path: Path, metadata: str
    ) -> None:
        root = _project(tmp_path, {"pyproject.toml": metadata})

        with pytest.raises(AnalysisError, match=r"pyproject\.toml"):
            check(root)

    def test_configured_mode_marks_unknown_workspace_coverage_incomplete(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace(
                    "coverage: strict", "coverage: configured"
                ),
                "pyproject.toml": "[invalid",
            },
        )

        outcome = check(root)

        assert not outcome.report.failed
        assert outcome.report.coverage is not None
        assert not outcome.report.coverage["complete"]
        assert outcome.report.coverage["discovery_errors"]

    def test_feature_only_architecture_does_not_need_invented_layers(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject: {root_packages: [app], source_roots: [src], coverage: strict}\narchitecture:\n  features: {root: app.features}\n",
                "src/app/__init__.py": "",
                "src/app/features/one/model.py": "VALUE = 1\n",
                "src/app/features/two/model.py": "VALUE = 2\n",
            },
        )

        assert violations(root) == []

    def test_deep_namespace_workspace_uses_the_src_import_root(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "pyproject.toml": '[tool.uv.workspace]\nmembers = ["libs/client"]\n',
                "libs/client/src/client/models/value.py": "VALUE = 1\n",
            },
        )

        [found] = violations(root)

        assert found.source_module == "client.models.value"
        assert found.expected is not None
        assert found.expected["source_root"] == "libs/client/src"

    def test_text_groups_one_missing_package_and_json_keeps_each_path(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path, {"src/client/first.py": "", "src/client/second.py": ""}
        )
        outcome = check(root)

        text = render_text(outcome.report, lambda path, line: "")

        assert len(outcome.report.violations) == 2
        assert text.count("SMT306 incomplete-coverage") == 1
        assert "2 source files have incomplete policy coverage" in text

    @pytest.mark.parametrize(
        "initializer",
        [
            '__version__: str = "1"\n',
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.domain.model import VALUE\n",
        ],
    )
    def test_typed_and_guarded_metadata_are_facades(
        self, tmp_path: Path, initializer: str
    ) -> None:
        root = _project(tmp_path, {"src/app/__init__.py": initializer})

        assert violations(root) == []

    def test_package_version_lookup_is_metadata_not_implementation(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "src/app/__init__.py": 'from importlib.metadata import version as package_version, PackageNotFoundError\ntry:\n    __version__ = package_version("example")\nexcept PackageNotFoundError:\n    __version__ = "0.0.0"\n'
            },
        )

        assert violations(root) == []

    def test_unselected_package_is_not_a_clean_check(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"src/client/api.py": "VALUE = 1\n"})

        [found] = violations(root)

        assert (found.code, found.path, found.source_module) == (
            "SMT306",
            "src/client/api.py",
            "client.api",
        )

    @pytest.mark.parametrize("exclude_uv_member", [False, True])
    def test_omitted_python_workspace_members_are_inventoried(
        self, tmp_path: Path, exclude_uv_member: bool
    ) -> None:
        workspace = '[tool.uv.workspace]\nmembers = ["libs/client", "docs"]\n'
        if exclude_uv_member:
            workspace += 'exclude = ["libs/client"]\n'
        root = _project(
            tmp_path,
            {
                "pyproject.toml": workspace,
                "libs/client/src/client/api.py": "VALUE = 1\n",
                "docs/README.md": "Documentation\n",
            },
        )

        [found] = violations(root)

        assert found.code == "SMT306"
        assert found.path == "libs/client/src/client/api.py"
        assert found.source_module == "client.api"

    def test_reasoned_exclusion_explains_an_omitted_member(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace(
                    "  coverage: strict",
                    '  coverage: strict\n  exclude: [{path: "libs/client/**", reason: "Generated SDK, validated by its generator"}]',
                ),
                "pyproject.toml": '[tool.uv.workspace]\nmembers = ["libs/client"]\n',
                "libs/client/src/client/api.py": "VALUE = 1\n",
            },
        )

        outcome = check(root, CheckOptions(show_coverage=True))

        assert not outcome.report.failed
        assert outcome.report.coverage is not None
        assert outcome.report.coverage["complete"] is True
        entry = next(
            item
            for item in outcome.report.coverage["inventory"]
            if item["path"] == "libs/client/src/client/api.py"
        )
        assert entry["status"] == "excluded"
        assert entry["reason"] == "Generated SDK, validated by its generator"

    def test_legacy_exclusions_need_a_reason_in_strict_mode(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace(
                    "  coverage: strict",
                    "  coverage: strict\n  exclude: ['src/client/**']",
                ),
                "src/client/api.py": "",
            },
        )

        [found] = violations(root)

        assert found.code == "SMT306"
        assert "no coverage reason" in found.message

    def test_configured_mode_reports_gaps_without_changing_exit_behavior(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace(
                    "coverage: strict", "coverage: configured"
                ),
                "src/client/api.py": "",
            },
        )

        outcome = check(root)

        assert not outcome.report.failed
        assert outcome.report.coverage is not None
        assert outcome.report.coverage["complete"] is False
        assert "inventory" not in outcome.report.coverage
        assert outcome.report.coverage["counts"]["uncovered"] == 1

    def test_entire_unclassified_library_cannot_escape(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace("[app]", "[app, client]"),
                "src/client/api.py": "VALUE = 1\n",
            },
        )

        [found] = violations(root)

        assert found.code == "SMT306"
        assert "unclassified" in found.message

    @pytest.mark.parametrize(
        "initializer",
        [
            "",
            '__version__ = "1"\n',
            '"""Public exports."""\nfrom app.domain.model import VALUE\n__all__ = ["VALUE"]\n',
        ],
    )
    def test_package_metadata_and_facades_do_not_need_fake_layers(
        self, tmp_path: Path, initializer: str
    ) -> None:
        root = _project(tmp_path, {"src/app/__init__.py": initializer})

        outcome = check(root, CheckOptions(show_coverage=True))

        assert not outcome.report.failed
        assert outcome.report.coverage is not None
        assert outcome.report.coverage["complete"] is True
        facade = next(
            item
            for item in outcome.report.coverage["inventory"]
            if item["path"] == "src/app/__init__.py"
        )
        assert facade["status"] == "facade"

    @pytest.mark.parametrize(
        "initializer",
        ["def make_service(): pass\n", "class Service: pass\n", "__all__ = list()\n"],
    )
    def test_implementation_in_an_initializer_is_not_a_pure_facade(
        self, tmp_path: Path, initializer: str
    ) -> None:
        root = _project(tmp_path, {"src/app/__init__.py": initializer})

        [found] = violations(root)

        assert found.code == "SMT306"
        assert found.path == "src/app/__init__.py"

    def test_known_library_policy_and_tests_are_not_gaps(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.replace("[app]", "[app, client]")
                + "  modules: {client: domain}\n",
                "src/client/api.py": "VALUE = 1\n",
                "tests/helpers.py": "VALUE = 1\n",
                "tests/test_system.py": "def test_behavior(): pass\n",
            },
        )

        outcome = check(root, CheckOptions(show_coverage=True))

        assert not outcome.report.failed
        assert outcome.report.coverage is not None
        entry = next(
            item
            for item in outcome.report.coverage["inventory"]
            if item["path"] == "src/client/api.py"
        )
        assert entry["status"] == "governed"
        assert "architecture.modules" in entry["policies"]
        assert not any(
            item["path"].startswith("tests/")
            for item in outcome.report.coverage["inventory"]
        )

    def test_scoping_and_rule_selection_cannot_hide_gaps(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"src/client/api.py": ""})

        [found] = violations(
            root, CheckOptions(paths=("src/app/domain/model.py",), select=("SMT101",))
        )

        assert (found.code, found.path) == ("SMT306", "src/client/api.py")

    def test_baseline_and_suppressions_cannot_accept_coverage_gaps(
        self, tmp_path: Path
    ) -> None:
        config = (
            CONFIG.replace("[app]", "[app, client]")
            + "ignore:\n  - rule: SMT306\n    paths: ['src/**']\n    reason: intentionally ignored\n"
        )
        root = _project(
            tmp_path,
            {
                "smelt.yaml": config,
                "src/client/api.py": "# smelt: ignore-file[SMT306] -- legacy\nVALUE = 1\n",
            },
        )
        initial = check(root)
        Debt.from_violations(initial.unfiltered, lambda v: "").write(root / "debt.json")
        (root / "smelt.yaml").write_text(config + "debt: debt.json\n", encoding="utf-8")

        outcome = check(root)

        assert outcome.report.failed
        assert any(v.code == "SMT306" for v in outcome.report.violations)
        assert outcome.report.in_debt == 0

    @pytest.mark.parametrize("setting", ["off", "warning", "hint"])
    def test_strict_coverage_cannot_be_demoted(
        self, tmp_path: Path, setting: str
    ) -> None:
        root = _project(
            tmp_path,
            {"smelt.yaml": CONFIG + f"rules: {{incomplete-coverage: {setting}}}\n"},
        )

        with pytest.raises(ConfigError, match="strict coverage must remain an error"):
            check(root)

    def test_ignoring_the_coverage_rule_is_a_usage_error(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {})

        with pytest.raises(InvalidOptionError, match="cannot disable strict coverage"):
            check(root, CheckOptions(ignore=("SMT306",)))
