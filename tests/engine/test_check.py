from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from smelt.analysis.imports import ImportIndex
from smelt.analysis.module_symbols import internal_module_symbols
from smelt.config import ConfigError
from smelt.diagnostics.debt import Debt, DebtEntry, fingerprint
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import (
    FIXTURES,
    LAYERED_CONFIG,
    LAYERED_POLICY,
    check,
    codes_at,
    violations,
    write_policy_project,
)

if TYPE_CHECKING:
    from pathlib import Path

GATEWAY_EXPECTED = [
    ("ARC004", "src/gateway/features/billing/application/invoices.py", 1),
    ("ARC006", "src/gateway/features/voice/api/routes.py", 3),
    ("ARC002", "src/gateway/features/voice/api/routes.py", 4),
    ("ARC003", "src/gateway/features/voice/application/session.py", 1),
    ("ARC001", "src/gateway/features/voice/application/session.py", 5),
    ("ARC003", "src/gateway/features/voice/domain/session.py", 4),
    ("ARC005", "src/gateway/shared/clock.py", 1),
]


def _layered(tmp_path: Path, service: str, config: str = LAYERED_CONFIG) -> Path:
    return write_policy_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/application/__init__.py": "",
            "app/infra/__init__.py": "",
            "app/infra/db.py": "",
            "app/application/service.py": service,
        },
    )


class TestGatewayFixture:
    def test_reports_every_dependency_violation(self) -> None:
        outcome = check(FIXTURES / "gateway")

        assert codes_at(outcome.report.violations) == GATEWAY_EXPECTED
        assert outcome.report.modules == 21

    def test_paths_restrict_reporting(self) -> None:
        found = violations(
            FIXTURES / "gateway",
            CheckOptions(paths=("src/gateway/features/voice/api",)),
        )

        assert {v.path for v in found} == {"src/gateway/features/voice/api/routes.py"}


class TestRuleSelection:
    def test_severity_override_and_off(self, tmp_path: Path) -> None:
        config = LAYERED_POLICY + "rules:\n  ARC001: warning\n"
        root = _layered(tmp_path, "from app.infra import db\n", config)

        outcome = check(root)

        assert [(v.code, v.severity) for v in outcome.report.violations] == [
            ("ARC001", Severity.WARNING)
        ]
        assert outcome.report.failed is False

    def test_fail_on_warning(self, tmp_path: Path) -> None:
        config = LAYERED_POLICY + "rules:\n  ARC001: warning\n"
        root = _layered(tmp_path, "from app.infra import db\n", config)

        assert check(root, CheckOptions(fail_on=Severity.WARNING)).report.failed is True

    def test_legacy_module_map_points_to_the_migration(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG.replace(
            "architecture:\n",
            "architecture:\n  modules: {app.infra.db: infrastructure}\n",
        )
        root = _layered(tmp_path, "", config)

        with pytest.raises(ConfigError, match="smelt config migrate") as caught:
            check(root)

        [issue] = caught.value.issues
        assert issue.path == "architecture.modules"
        assert "architecture.layers.<layer>.modules" in issue.message
        assert "docs/upgrading.md" in str(caught.value)

    def test_rule_turned_off(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path,
            "from app.infra import db\n",
            LAYERED_POLICY + "rules:\n  ARC001: off\n",
        )

        assert violations(root) == []

    def test_ignore_option(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "from app.infra import db\n")

        assert violations(root, CheckOptions(ignore=("ARC",))) == []

    def test_unknown_rule_in_config(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "", LAYERED_POLICY + "rules:\n  SMT199: off\n")

        with pytest.raises(ConfigError, match='unknown rule "SMT199"'):
            check(root)

    def test_rule_configured_by_name(self, tmp_path: Path) -> None:
        config = LAYERED_POLICY + "rules:\n  architecture/layer-boundary: warning\n"
        root = _layered(tmp_path, "from app.infra import db\n", config)

        found = violations(root)

        assert [(v.code, v.severity) for v in found] == [("ARC001", Severity.WARNING)]

    def test_unknown_rule_name_suggests_a_match(self, tmp_path: Path) -> None:
        config = LAYERED_POLICY + "rules:\n  layer-boundry: off\n"
        root = _layered(tmp_path, "", config)

        with pytest.raises(
            ConfigError, match='did you mean "architecture/layer-boundary"'
        ):
            check(root)

    def test_rule_configured_by_code_and_name(self, tmp_path: Path) -> None:
        config = (
            LAYERED_POLICY
            + "rules:\n  ARC001: off\n  architecture/layer-boundary: error\n"
        )
        root = _layered(tmp_path, "", config)

        with pytest.raises(ConfigError, match="ARC001 is already configured"):
            check(root)


class TestIgnoreEntries:
    def test_module_glob(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + (
            "ignore:\n  - rule: ARC001\n    modules: ['app.application.**']\n    reason: legacy\n"
        )
        root = _layered(tmp_path, "from app.infra import db\n", config)

        assert violations(root) == []

    def test_path_glob(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + (
            "ignore:\n  - rule: ARC\n    paths: ['app/application/*.py']\n    reason: legacy\n"
        )
        root = _layered(tmp_path, "from app.infra import db\n", config)

        assert violations(root) == []


class TestSuppressions:
    @pytest.mark.parametrize("kind", ["assertion", "assignment", "return"])
    def test_interior_expression_exception_survives_ruff(
        self, tmp_path: Path, kind: str
    ) -> None:
        expression = (
            "(\n    service._state()\n"
            "    is None  # smelt: ignore[TST002] -- deliberate internal seam\n)\n"
        )
        statement = {
            "assertion": "assert " + expression,
            "assignment": "result = " + expression,
            "return": "return " + expression,
        }[kind]
        source = (
            "import app.service as service\ndef test_state():\n"
            + textwrap.indent(statement, "    ")
            + "    service._state()\n"
        )
        formatted = subprocess.run(
            [sys.executable, "-m", "ruff", "format", "-"],
            input=source,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert formatted.returncode == 0, formatted.stderr
        write_policy_project(
            tmp_path,
            {
                "smelt.yml": (
                    "version: 2\nproject: {root_packages: [app]}\n"
                    "rules: {tests/private-access: error, meta/unused-suppression: error}\n"
                ),
                "app/__init__.py": "",
                "app/service.py": "def _state(): return None\n",
                "tests/test_service.py": formatted.stdout,
            },
        )

        outcome = check(tmp_path)

        assert outcome.report.suppressed == 1
        [remaining] = outcome.report.violations
        assert remaining.code == "TST002"
        assert remaining.line == len(formatted.stdout.splitlines())

    def test_ruff_format_preserves_a_definition_exception(self, tmp_path: Path) -> None:
        source = "def public_function(first_parameter: int, second_parameter: int, third_parameter: int):  # smelt: ignore[VIS001] -- deliberate external API\n    return first_parameter\n"
        write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {visibility/internal-public-member: warning}\n",
                "app/__init__.py": "",
                "app/api.py": source,
            },
        )
        assert check(tmp_path).report.suppressed == 1
        formatted = subprocess.run(
            [sys.executable, "-m", "ruff", "format", "--line-length", "40", "-"],
            input=source,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert formatted.returncode == 0, formatted.stderr
        assert formatted.stdout != source
        (tmp_path / "app/api.py").write_text(formatted.stdout, encoding="utf-8")

        outcome = check(tmp_path)
        assert outcome.report.violations == []
        assert outcome.report.suppressed == 1

    def test_signature_suppression_does_not_cover_function_body(
        self, tmp_path: Path
    ) -> None:
        write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {visibility/internal-public-member: warning, idioms/bundled-instance-assignment: warning}\n",
                "app/__init__.py": "",
                "app/worker.py": "class Worker:\n    def helper(\n        self,\n    ):  # smelt: ignore[VIS001, IDM001] -- deliberate API\n        self.first = self.second = 0\n",
            },
        )
        outcome = check(tmp_path)

        assert outcome.report.suppressed == 1
        assert {(v.code, v.line) for v in outcome.report.violations} == {
            ("IDM001", 5),
            ("MET001", 4),
        }

    @pytest.mark.parametrize("comment_line", ["opening", "closing"])
    def test_multiline_statement_suppression_survives_formatting(
        self, tmp_path: Path, comment_line: str
    ) -> None:
        comment = "  # smelt: ignore[ARC001] -- migration"
        source = (
            "from app.infra import ("
            + (comment if comment_line == "opening" else "")
            + "\n    db,\n)"
            + (comment if comment_line == "closing" else "")
            + "\n"
        )
        outcome = check(_layered(tmp_path, source))

        assert outcome.report.violations == []
        assert outcome.report.suppressed == 1

    def test_statement_suppression_does_not_cover_the_next_statement(
        self, tmp_path: Path
    ) -> None:
        source = "from app.infra import (\n    db,\n)  # smelt: ignore[ARC001] -- migration\nfrom app.infra import db\n"
        outcome = check(_layered(tmp_path, source))

        assert [(v.code, v.line) for v in outcome.report.violations] == [("ARC001", 4)]
        assert outcome.report.suppressed == 1

    def test_inline_suppression_with_reason(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path, "from app.infra import db  # smelt: ignore[ARC001] -- migrating\n"
        )

        outcome = check(root)

        assert outcome.report.violations == []
        assert outcome.report.suppressed == 1

    def test_suppression_without_reason(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "from app.infra import db  # smelt: ignore[ARC001]\n")

        assert codes_at(violations(root)) == [
            ("MET002", "app/application/service.py", 1)
        ]

    def test_file_level_prefix_suppression(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path,
            "# smelt: ignore-file[ARC] -- generated\nfrom app.infra import db\n",
        )

        assert violations(root) == []

    def test_unused_suppression(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "import os  # smelt: ignore[ARC001] -- stale\n")

        [found] = violations(root)

        assert (found.code, found.message) == (
            "MET001",
            "unused suppression (all codes)",
        )

    def test_partially_used_suppression(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path, "from app.infra import db  # smelt: ignore[ARC001, ARC003] -- x\n"
        )

        [found] = violations(root)

        assert found.message == "unused suppression (ARC003)"

    def test_rule_name_suppresses_its_finding(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path,
            "from app.infra import db  "
            "# smelt: ignore[architecture/layer-boundary] -- migration\n",
        )

        outcome = check(root)

        assert outcome.report.suppressed == 1
        assert outcome.report.violations == []

    def test_unused_rule_name_is_reported_as_written(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path,
            "from app.infra import db  # smelt: ignore[ARC001, "
            "architecture/forbidden-third-party-import] -- x\n",
        )

        [found] = violations(root)

        assert found.message == (
            "unused suppression (architecture/forbidden-third-party-import)"
        )

    def test_suppression_for_unselected_rule_is_not_unused(
        self, tmp_path: Path
    ) -> None:
        root = _layered(
            tmp_path,
            "import os  # smelt: ignore[ARC004] -- x\n",
            LAYERED_POLICY + "rules:\n  ARC004: error\n",
        )

        assert violations(root, CheckOptions(select=("ARC001", "MET001"))) == []

    def test_unused_blanket_suppression(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "import os  # smelt: ignore -- stale\n")

        [found] = violations(root)

        assert (found.code, found.message) == (
            "MET001",
            "unused suppression (all codes)",
        )

    def test_blanket_suppression_in_a_narrowed_run(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "import os  # smelt: ignore -- stale\n")

        assert violations(root, CheckOptions(select=("ARC001", "MET001"))) == []

    def test_suppression_for_a_rule_that_is_off(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path,
            "import os  # smelt: ignore[ARC012] -- x\n",
            LAYERED_POLICY + "rules:\n  ARC012: off\n",
        )

        [found] = violations(root)

        assert found.message == "unused suppression (all codes)"


@pytest.mark.parametrize(
    ("identifier", "reported"),
    [
        ("XYZ999", "XYZ999"),
        ("arcx", "ARCX"),
        ("ARC0010", "ARC0010"),
        ("tests/no-such-finding", "tests/no-such-finding"),
    ],
)
def test_unknown_suppression_identifiers_are_diagnosed_in_narrowed_checks(
    tmp_path: Path, identifier: str, reported: str
) -> None:
    root = _layered(tmp_path, f"import os  # smelt: ignore[{identifier}] -- reviewed\n")
    [finding] = violations(root, CheckOptions(select=("ARC001", "MET001")))
    assert finding.code == "MET001"
    assert finding.expected == {"unknown_identifiers": [reported]}
    assert finding.line == 1
    assert finding.hint == ("Use a rule name or a rule code; `smelt rules` lists both.")


def test_retired_rule_name_in_a_suppression_names_its_replacement(
    tmp_path: Path,
) -> None:
    root = _layered(
        tmp_path, "from app.infra import db  # smelt: ignore[layer-boundary] -- x\n"
    )

    unknown = next(
        finding
        for finding in violations(root)
        if finding.message.startswith("unknown suppression")
    )

    assert unknown.expected == {"unknown_identifiers": ["layer-boundary"]}
    assert 'renamed to "architecture/layer-boundary"' in (unknown.hint or "")


def test_misspelled_rule_name_in_a_suppression_suggests_the_rule(
    tmp_path: Path,
) -> None:
    root = _layered(
        tmp_path, "import os  # smelt: ignore[architecture/layer-boundry] -- x\n"
    )

    [finding] = violations(root, CheckOptions(select=("ARC001", "MET001")))

    assert finding.hint == (
        'architecture/layer-boundry (did you mean "architecture/layer-boundary"?).'
    )


def test_unknown_identifier_does_not_hide_a_valid_suppression(tmp_path: Path) -> None:
    root = _layered(
        tmp_path,
        "from app.infra import db  # smelt: ignore[ARC001, SMT999] -- reviewed\n",
    )
    outcome = check(root)
    assert outcome.report.suppressed == 1
    assert [finding.code for finding in outcome.report.violations] == ["MET001"]


class TestDebt:
    def test_existing_debt_with_comments_remains_readable(self, tmp_path: Path) -> None:
        source = "from app.infra import db  # old comment\n"
        root = _layered(tmp_path, source, LAYERED_CONFIG + "debt: debt.json\n")
        [found] = check(root).unfiltered
        Debt(
            [
                DebtEntry(
                    fingerprint(found, source, legacy=True),
                    found.code,
                    found.path,
                    found.message,
                )
            ]
        ).write(root / "debt.json")

        assert check(root).report.in_debt == 1
        assert check(root).report.violations == []

    def test_import_comments_do_not_invalidate_debt(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path, "from app.infra import db\n", LAYERED_CONFIG + "debt: debt.json\n"
        )
        outcome = check(root)
        Debt.from_violations(
            outcome.unfiltered,
            lambda v: outcome.context.files.line(v.path or "", v.line or 0),
        ).write(root / "debt.json")
        (root / "app/application/service.py").write_text(
            "from app.infra import db  # updated documentation\n", encoding="utf-8"
        )

        assert check(root).report.in_debt == 1
        assert check(root).report.violations == []

    def test_known_violations_pass_and_survive_line_shifts(
        self, tmp_path: Path
    ) -> None:
        config = LAYERED_CONFIG + "debt: .smelt/debt.json\n"
        root = _layered(tmp_path, "from app.infra import db\n", config)
        outcome = check(root)
        Debt.from_violations(
            outcome.unfiltered,
            lambda v: outcome.context.files.line(v.path or "", v.line or 0),
        ).write(root / ".smelt" / "debt.json")
        (root / "app/application/service.py").write_text(
            "import os\n\n\nfrom app.infra import db\n"
        )

        shifted = check(root)

        assert shifted.report.violations == []
        assert shifted.report.in_debt == 1

    def test_resolved_entries_are_reported(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + "debt: debt.json\n"
        root = _layered(tmp_path, "", config)
        (root / "debt.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "violations": [
                        {
                            "fingerprint": "abc",
                            "code": "ARC001",
                            "path": "x.py",
                            "message": "gone",
                        }
                    ],
                }
            )
        )

        assert [v.code for v in violations(root)] == ["MET003"]


def test_global_rules_build_shared_import_and_symbol_facts_once(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": (
                "version: 2\nproject: {root_packages: [app]}\n"
                "architecture:\n  packages:\n"
                "    api: {match: app.api, public: ['.'], min_imports_to_bundle: 2}\n"
            ),
            "app/__init__.py": "",
            "app/api/__init__.py": "",
            "app/api/models.py": "LIMIT = 1\n",
            "tests/test_api.py": "from app.api.models import LIMIT\n",
        },
    )
    with (
        patch.object(
            ImportIndex,
            "imports_in_tests",
            autospec=True,
            side_effect=ImportIndex.imports_in_tests,
        ) as test_imports,
        patch(
            "smelt.rules.structure.module_symbols.internal_module_symbols",
            wraps=internal_module_symbols,
        ) as symbols,
    ):
        found = violations(root, CheckOptions(select=("ARC007", "IMP001", "VIS002")))
    assert any(
        item.code == "ARC007" and item.path == "tests/test_api.py" for item in found
    )
    test_imports.assert_called_once()
    symbols.assert_called_once()


def test_retired_smt_code_in_a_suppression_names_its_family_code(
    tmp_path: Path,
) -> None:
    root = _layered(tmp_path, "import os  # smelt: ignore[SMT101] -- reviewed\n")

    [finding] = violations(root, CheckOptions(select=("ARC001", "MET001")))

    assert finding.expected == {"unknown_identifiers": ["SMT101"]}
    assert finding.hint is not None
    assert 'renamed to ARC001 "architecture/layer-boundary"' in finding.hint
