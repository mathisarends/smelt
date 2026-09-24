from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.baseline import Baseline
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import (
    FIXTURES,
    LAYERED_CONFIG,
    check,
    codes_at,
    violations,
    write_project,
)

if TYPE_CHECKING:
    from pathlib import Path

GATEWAY_EXPECTED = [
    ("SMT104", "src/gateway/features/billing/application/invoices.py", 1),
    ("SMT106", "src/gateway/features/voice/api/routes.py", 3),
    ("SMT102", "src/gateway/features/voice/api/routes.py", 4),
    ("SMT106", "src/gateway/features/voice/application/session.py", 1),
    ("SMT101", "src/gateway/features/voice/application/session.py", 5),
    ("SMT103", "src/gateway/features/voice/domain/session.py", 4),
    ("SMT105", "src/gateway/shared/clock.py", 1),
]


def _layered(tmp_path: Path, service: str, config: str = LAYERED_CONFIG) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": config,
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
        config = LAYERED_CONFIG + "rules:\n  SMT101: warning\n"
        root = _layered(tmp_path, "from app.infra import db\n", config)

        outcome = check(root)

        assert [(v.code, v.severity) for v in outcome.report.violations] == [
            ("SMT101", Severity.WARNING)
        ]
        assert outcome.report.failed is False

    def test_fail_on_warning(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + "rules:\n  SMT101: warning\n"
        root = _layered(tmp_path, "from app.infra import db\n", config)

        assert check(root, CheckOptions(fail_on=Severity.WARNING)).report.failed is True

    def test_rule_turned_off(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path,
            "from app.infra import db\n",
            LAYERED_CONFIG + "rules:\n  SMT101: off\n",
        )

        assert violations(root) == []

    def test_ignore_option(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "from app.infra import db\n")

        assert violations(root, CheckOptions(ignore=("SMT1",))) == []

    def test_unknown_rule_in_config(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "", LAYERED_CONFIG + "rules:\n  SMT199: off\n")

        with pytest.raises(ConfigError, match='unknown rule "SMT199"'):
            check(root)


class TestIgnoreEntries:
    def test_module_glob(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + (
            "ignore:\n  - rule: SMT101\n    modules: ['app.application.**']\n    reason: legacy\n"
        )
        root = _layered(tmp_path, "from app.infra import db\n", config)

        assert violations(root) == []

    def test_path_glob(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + (
            "ignore:\n  - rule: SMT1\n    paths: ['app/application/*.py']\n    reason: legacy\n"
        )
        root = _layered(tmp_path, "from app.infra import db\n", config)

        assert violations(root) == []


class TestSuppressions:
    def test_inline_suppression_with_reason(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path, "from app.infra import db  # smelt: ignore[SMT101] -- migrating\n"
        )

        outcome = check(root)

        assert outcome.report.violations == []
        assert outcome.report.suppressed == 1

    def test_suppression_without_reason(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "from app.infra import db  # smelt: ignore[SMT101]\n")

        assert codes_at(violations(root)) == [
            ("SMT902", "app/application/service.py", 1)
        ]

    def test_file_level_prefix_suppression(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path,
            "# smelt: ignore-file[SMT1] -- generated\nfrom app.infra import db\n",
        )

        assert violations(root) == []

    def test_unused_suppression(self, tmp_path: Path) -> None:
        root = _layered(tmp_path, "import os  # smelt: ignore[SMT101] -- stale\n")

        [found] = violations(root)

        assert (found.code, found.message) == (
            "SMT901",
            "unused suppression (all codes)",
        )

    def test_partially_used_suppression(self, tmp_path: Path) -> None:
        root = _layered(
            tmp_path, "from app.infra import db  # smelt: ignore[SMT101, SMT103] -- x\n"
        )

        [found] = violations(root)

        assert found.message == "unused suppression (SMT103)"

    def test_suppression_for_unselected_rule_is_not_unused(
        self, tmp_path: Path
    ) -> None:
        root = _layered(tmp_path, "import os  # smelt: ignore[SMT104] -- x\n")

        assert violations(root, CheckOptions(select=("SMT101", "SMT901"))) == []


class TestBaseline:
    def test_known_violations_pass_and_survive_line_shifts(
        self, tmp_path: Path
    ) -> None:
        config = LAYERED_CONFIG + "baseline: .smelt/baseline.json\n"
        root = _layered(tmp_path, "from app.infra import db\n", config)
        outcome = check(root)
        Baseline.from_violations(
            outcome.unfiltered,
            lambda v: outcome.context.files.line(v.path or "", v.line or 0),
        ).write(root / ".smelt" / "baseline.json")
        (root / "app/application/service.py").write_text(
            "import os\n\n\nfrom app.infra import db\n"
        )

        shifted = check(root)

        assert shifted.report.violations == []
        assert shifted.report.baselined == 1

    def test_stale_entries_are_reported(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + "baseline: baseline.json\n"
        root = _layered(tmp_path, "", config)
        (root / "baseline.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "violations": [
                        {
                            "fingerprint": "abc",
                            "code": "SMT101",
                            "path": "x.py",
                            "message": "gone",
                        }
                    ],
                }
            )
        )

        assert [v.code for v in violations(root)] == ["SMT903"]
