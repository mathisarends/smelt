from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import load_config
from smelt.engine.briefing import (
    TargetError,
    build_briefing,
    render_briefing,
    resolve_target,
)
from smelt.engine.check import CheckOptions, CheckOutcome, run_check
from tests.helpers import FIXTURES, check, write_project

if TYPE_CHECKING:
    from pathlib import Path

GATEWAY = FIXTURES / "gateway"


@pytest.fixture(scope="module")
def outcome() -> CheckOutcome:
    return run_check(load_config(GATEWAY / "smelt.yaml"), CheckOptions())


class TestResolveTarget:
    def test_feature_name(self, outcome: CheckOutcome) -> None:
        target = resolve_target(outcome.context, "voice")

        assert (target.kind, target.feature) == ("feature", "voice")

    def test_source_file_path(self, outcome: CheckOutcome) -> None:
        target = resolve_target(
            outcome.context, "src/gateway/features/billing/domain/money.py"
        )

        assert (target.kind, target.feature, target.module) == (
            "module",
            "billing",
            "gateway.features.billing.domain.money",
        )

    def test_package_directory(self, outcome: CheckOutcome) -> None:
        target = resolve_target(outcome.context, "src/gateway/shared/")

        assert (target.kind, target.module) == ("package", "gateway.shared")

    def test_unknown_target_suggests_feature(self, outcome: CheckOutcome) -> None:
        with pytest.raises(TargetError, match=r'did you mean "voice"\?'):
            resolve_target(outcome.context, "voic")


class TestFeatureBriefing:
    def test_text_matches_architecture(self, outcome: CheckOutcome) -> None:
        ctx = outcome.context
        briefing = build_briefing(ctx, resolve_target(ctx, "voice"), outcome.report)

        assert render_briefing(briefing, ctx) == (
            "Feature: voice   (gateway.features.voice)\n"
            "\n"
            "Layers:\n"
            "  domain         → (nothing)            third-party: only pydantic\n"
            "  application    → domain               third-party: not fastapi, sqlalchemy, dishka\n"
            "  infrastructure → domain, application\n"
            "  presentation   → application\n"
            "Cross-feature:    only application → application\n"
            "Shared:           gateway.shared (must not import features)\n"
            "Composition root: gateway.bootstrap, gateway.main\n"
            "\n"
            "Tests: no layout enforced\n"
            "\n"
            "Current violations: 5 errors (SMT101, SMT102, SMT103, SMT106)\n"
        )

    def test_json_counts_only_feature_violations(self, outcome: CheckOutcome) -> None:
        ctx = outcome.context
        briefing = build_briefing(ctx, resolve_target(ctx, "billing"), outcome.report)

        data = briefing.to_json()
        assert data["violations"] == {
            "errors": 1,
            "warnings": 0,
            "hints": 0,
            "codes": ["SMT104"],
        }
        assert data["layers"][0]["package"] == "gateway.features.billing.domain"


class TestModuleBriefing:
    def test_marks_current_layer(self, outcome: CheckOutcome) -> None:
        ctx = outcome.context
        target = resolve_target(
            ctx, "src/gateway/features/voice/application/session.py"
        )

        text = render_briefing(build_briefing(ctx, target, outcome.report), ctx)

        assert "Kind: feature voice, layer application\n" in text
        assert " *application    → domain" in text
        assert text.endswith("Current violations: 2 errors (SMT101, SMT103)\n")

    def test_shared_module_kind(self, outcome: CheckOutcome) -> None:
        ctx = outcome.context
        target = resolve_target(ctx, "src/gateway/shared/clock.py")

        text = render_briefing(build_briefing(ctx, target, outcome.report), ctx)

        assert "Kind: shared (importable by all features" in text


MIRRORED = {
    "smelt.yaml": """
        version: 1
        project: {root_packages: [app]}
        architecture:
          features: {root: app.features}
          wiring: [app.features.*.infra.di]
          modules: {app.platform: infra}
          layers:
            domain: {path: domain}
            infra: {path: infra, may_depend_on: [domain]}
        tests:
          layout: mirror
          mirror: "{root}/{path}/test_{module}.py"
    """,
    "app/__init__.py": "",
    "app/misc.py": "",
    "app/platform/__init__.py": "",
    "app/platform/orm.py": "",
    "app/features/__init__.py": "",
    "app/features/voice/__init__.py": "",
    "app/features/voice/domain/__init__.py": "",
    "app/features/voice/domain/call.py": "",
    "app/features/voice/infra/__init__.py": "",
    "app/features/voice/infra/di.py": "",
    "app/features/billing/__init__.py": "",
    "app/features/billing/domain/__init__.py": "",
    "tests/__init__.py": "",
}


def _briefing(tmp_path: Path, target: str) -> str:
    outcome = check(write_project(tmp_path, MIRRORED))
    ctx = outcome.context
    briefing = build_briefing(ctx, resolve_target(ctx, target), outcome.report)
    return render_briefing(briefing, ctx)


class TestMirroredProject:
    def test_feature_names_its_test_directory(self, tmp_path: Path) -> None:
        text = _briefing(tmp_path, "voice")

        assert "Wiring:           app.features.*.infra.di\n" in text
        assert (
            "Tests: mirror at tests/app/features/voice/<layer>/test_<module>.py" in text
        )

    def test_feature_without_wiring_hides_it(self, tmp_path: Path) -> None:
        assert "Wiring:" not in _briefing(tmp_path, "billing")

    def test_module_names_its_test_file(self, tmp_path: Path) -> None:
        text = _briefing(tmp_path, "app/features/voice/domain/call.py")

        assert "Tests: mirror at tests/app/features/voice/domain/test_call.py" in text

    def test_central_module(self, tmp_path: Path) -> None:
        text = _briefing(tmp_path, "app/platform/orm.py")

        assert "Kind: central module in layer infra (outside the features" in text

    def test_unclassified_module_lists_no_policy(self, tmp_path: Path) -> None:
        text = _briefing(tmp_path, "app/misc.py")

        assert "No boundary rule covers this module" in text
        assert "Layers:" not in text

    def test_unknown_target_lists_the_features(self, tmp_path: Path) -> None:
        outcome = check(write_project(tmp_path, MIRRORED))

        with pytest.raises(TargetError, match=r"; features: billing, voice$"):
            resolve_target(outcome.context, "nothing")
