from pathlib import Path

import pytest

from smelt.config import ConfigError
from smelt.config.loader import LoadedConfig
from smelt.config.models import SmeltConfig
from smelt.engine.check import (
    CheckOptions,
    InvalidOptionError,
    resolve_active_rules,
    rule_meta,
)
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule
from smelt.rules.registry import build_rule_set

BASELINE = {"SMT301", "SMT901", "SMT902", "SMT903"}


def _active(extra: dict[str, object], options: CheckOptions | None = None) -> set[str]:
    config = SmeltConfig.model_validate(
        {"version": 1, "project": {"root_packages": ["app"]}, **extra}
    )
    loaded = LoadedConfig(config, Path("smelt.yaml"))
    return {
        rule.code
        for rule, _ in resolve_active_rules(
            build_rule_set(), loaded, options or CheckOptions()
        )
    }


def test_minimal_configuration_runs_only_the_small_baseline() -> None:
    assert _active({}) == BASELINE
    assert not BaseRule.enabled_by_default
    assert BaseRule.activation is None


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"architecture": {"layers": {"domain": {"path": "domain"}}}}, {"SMT101"}),
        ({"architecture": {"features": {"root": "app.features"}}}, {"SMT102"}),
        (
            {
                "architecture": {
                    "layers": {"domain": {"path": "domain", "third_party": "deny"}}
                }
            },
            {"SMT101", "SMT103"},
        ),
        ({"architecture": {"shared": ["app.shared"]}}, {"SMT105"}),
        ({"architecture": {"modules": {"app.platform": "infrastructure"}}}, {"SMT105"}),
        ({"architecture": {"composition_root": ["app.main"]}}, {"SMT106"}),
        ({"architecture": {"wiring": ["app.di"]}}, {"SMT106"}),
        ({"structure": {"forbidden_names": ["utils"]}}, {"SMT302"}),
        ({"project": {"root_packages": ["app"], "coverage": "strict"}}, {"SMT306"}),
        ({"tests": {"layout": "mirror"}}, {"SMT401"}),
        ({"tests": {"forbid_private_imports": True}}, {"SMT420"}),
        ({"tests": {"forbid_mocks_in": ["tests/domain/**"]}}, {"SMT421"}),
        ({"tests": {"forbid_string_patch_targets": True}}, {"SMT422"}),
        ({"tests": {"max_patches_per_test": 0}}, {"SMT423"}),
        ({"tests": {"require_assertions": True}}, {"SMT424"}),
        ({"tests": {"require_non_vacuous_assertions": True}}, {"SMT425"}),
        ({"tests": {"review_test_deadlines": True}}, {"SMT426"}),
        ({"tests": {"review_case_loops": True}}, {"SMT427"}),
        ({"tests": {"review_async_mock_assertions": True}}, {"SMT428"}),
    ],
)
def test_explicit_project_policy_activates_its_checks(
    extra: dict[str, object], expected: set[str]
) -> None:
    assert _active(extra) == BASELINE | expected


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({"public": []}, {"SMT107"}),
        ({"public": ["."]}, {"SMT107", "SMT307"}),
        ({"required": ["api"]}, {"SMT307"}),
        ({"bundle_imports_at": 3}, {"SMT108"}),
        ({"imports": {"style": "absolute"}}, {"SMT308"}),
        ({"module_docstrings": "forbid"}, {"SMT309"}),
        ({"future_annotations": "forbid"}, {"SMT310"}),
        ({"future_annotations": "when-needed"}, {"SMT310", "SMT311"}),
        ({"private_modules": "forbid"}, {"SMT312"}),
        ({"ordering": {}}, {"SMT313"}),
        ({"instance_assignments": "separate"}, {"SMT314"}),
        ({"type_dispatch": {}}, {"SMT315"}),
        (
            {
                "module_docstrings": "allow",
                "future_annotations": "allow",
                "private_modules": "allow",
                "ordering": {"enabled": False},
                "instance_assignments": "allow",
                "type_dispatch": {"style": "allow"},
            },
            set(),
        ),
    ],
)
def test_package_policy_activates_without_a_second_rule_switch(
    policy: dict[str, object], expected: set[str]
) -> None:
    assert (
        _active({"structure": {"contracts": {"api": {"match": "app", **policy}}}})
        == BASELINE | expected
    )


def test_scope_settings_alone_do_not_enable_explicit_heuristics() -> None:
    assert _active({"architecture": {"imports": {"cycles": ["siblings"]}}}) == BASELINE
    assert _active(
        {"rules": {"SMT104": "error", "unclassified-module": "warning"}}
    ) == BASELINE | {"SMT104", "SMT305"}


def test_prefix_selection_enables_opt_in_rules_and_replaces_normal_selection() -> None:
    expected = {
        rule.code for rule in build_rule_set().rules if rule.code.startswith("SMT3")
    }
    assert _active({}, CheckOptions(select=("smt3",))) == expected


def test_all_selects_every_supported_rule() -> None:
    assert _active({}, CheckOptions(select=("ALL",))) == {
        rule.code for rule in build_rule_set().rules
    }


def test_off_and_ignore_win_over_explicit_selection() -> None:
    active = _active(
        {"rules": {"SMT104": "off"}}, CheckOptions(select=("ALL",), ignore=("SMT3",))
    )
    assert "SMT104" not in active
    assert not any(code.startswith("SMT3") for code in active)
    assert _active({}, CheckOptions(ignore=("ALL",))) == set()


@pytest.mark.parametrize("option", ["off", "ignore"])
def test_all_cannot_silence_explicit_strict_coverage(option: str) -> None:
    extra: dict[str, object] = {
        "project": {"root_packages": ["app"], "coverage": "strict"}
    }
    options = CheckOptions(select=("SMT1",))
    if option == "off":
        extra["rules"] = {"SMT306": "off"}
    else:
        options = CheckOptions(ignore=("ALL",))
    with pytest.raises((ConfigError, InvalidOptionError), match="strict coverage"):
        _active(extra, options)


def test_strict_coverage_still_runs_outside_a_select_prefix() -> None:
    assert "SMT306" in _active(
        {"project": {"root_packages": ["app"], "coverage": "strict"}},
        CheckOptions(select=("SMT1",)),
    )


def test_catalog_exposes_every_rule_activation_decision() -> None:
    modes = {
        rule.code: rule_meta(rule).activation["mode"] for rule in build_rule_set().rules
    }
    assert {code for code, mode in modes.items() if mode == "default"} == BASELINE
    assert modes["SMT104"] == "explicit"
    assert modes["SMT305"] == "explicit"
    for rule in build_rule_set().rules:
        activation = getattr(rule, "activation", None)
        if activation is not None:
            assert isinstance(activation, PolicyActivation)
            assert activation.description.strip()
            assert not rule_meta(rule).enabled_by_default
