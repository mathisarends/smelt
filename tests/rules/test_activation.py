from pathlib import Path

import pytest

from smelt.config import ConfigError
from smelt.config.loader import LoadedConfig
from smelt.config.models import SmeltConfig
from smelt.engine.check import (
    CheckOptions,
    InvalidOptionError,
    _resolve_active_rules,  # smelt: ignore[SMT420] -- Exercise the private analysis primitive and its safety boundaries directly.
    rule_meta,
)
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import Rule
from smelt.rules.registry import build_rule_set

BASELINE = {"SMT301", "SMT901", "SMT902", "SMT903"}


def _active(extra: dict[str, object], options: CheckOptions | None = None) -> set[str]:
    config = SmeltConfig.model_validate(
        {"version": 1, "project": {"root_packages": ["app"]}, **extra}
    )
    loaded = LoadedConfig(config, Path("smelt.yml"))
    return {
        rule.code
        for rule, _ in _resolve_active_rules(
            build_rule_set(), loaded, options or CheckOptions()
        )
    }


def test_minimal_configuration_runs_only_the_small_baseline() -> None:
    assert _active({}) == BASELINE
    assert not Rule.enabled_by_default
    assert Rule.activation is None


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
        ({"style": {"forbidden_names": ["utils"]}}, {"SMT302"}),
        ({"style": {"max_module_imports": 0}}, {"SMT110"}),
        ({"style": {"file_naming": {"yaml_extension": "yaml"}}}, {"SMT331"}),
        ({"style": {"file_naming": {"compose_basename": "compose"}}}, {"SMT331"}),
        ({"project": {"root_packages": ["app"], "coverage": "strict"}}, {"SMT306"}),
        ({"tests": {"layout": "mirror"}}, {"SMT401"}),
        ({"rules": {"tests/private-access": "error"}}, {"SMT420"}),
        ({"tests": {"forbidden_mock_paths": ["tests/domain/**"]}}, {"SMT421"}),
        ({"rules": {"tests/string-patch-target": "error"}}, {"SMT422"}),
        ({"tests": {"max_patches_per_test": 0}}, {"SMT423"}),
        ({"rules": {"tests/missing-outcome-check": "warning"}}, {"SMT424"}),
        ({"rules": {"tests/vacuous-assertion": "warning"}}, {"SMT425"}),
        ({"rules": {"tests/unbounded-wait": "warning"}}, {"SMT426"}),
        ({"rules": {"tests/independent-case-loop": "hint"}}, {"SMT427"}),
        ({"rules": {"tests/missing-await-assertion": "warning"}}, {"SMT428"}),
        ({"rules": {"visibility/test-only-public-function": "warning"}}, {"SMT429"}),
        ({"rules": {"visibility/test-only-public-attribute": "warning"}}, {"SMT430"}),
        ({"rules": {"complexity/excess-attribute-depth": "hint"}}, {"SMT322"}),
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
        ({"min_imports_to_bundle": 3}, {"SMT108"}),
        ({"max_module_imports": 0}, {"SMT110"}),
        ({"min_importers_to_export": 3}, {"SMT109"}),
        ({"min_importers_to_export": 3, "count_tests": True}, {"SMT109"}),
        ({"imports": {"style": "absolute"}}, {"SMT308"}),
        ({"module_docstrings": "forbid"}, {"SMT309"}),
        ({"module_docstrings": "require"}, {"SMT309"}),
        ({"future_annotations": "forbid"}, {"SMT310"}),
        ({"future_annotations": "when-needed"}, {"SMT310", "SMT311"}),
        ({"private_modules": "forbid"}, {"SMT312"}),
        ({"ordering": {}, "rules": {"layout/definition-order": "error"}}, {"SMT313"}),
        ({"rules": {"idioms/bundled-instance-assignment": "error"}}, {"SMT314"}),
        ({"type_dispatch": {}}, {"SMT315"}),
        ({"rules": {"visibility/internal-public-member": "warning"}}, {"SMT316"}),
        ({"rules": {"layout/misplaced-static-helper": "hint"}}, {"SMT317"}),
        ({"rules": {"complexity/excess-attribute-depth": "hint"}}, {"SMT322"}),
        (
            {
                "module_docstrings": "allow",
                "future_annotations": "allow",
                "private_modules": "allow",
                "ordering": {},
                "type_dispatch": {"style": "allow"},
                "rules": {
                    "visibility/internal-public-member": "off",
                    "layout/misplaced-static-helper": "off",
                    "idioms/bundled-instance-assignment": "off",
                    "layout/definition-order": "off",
                },
            },
            set(),
        ),
    ],
)
def test_package_policy_activates_without_a_second_rule_switch(
    policy: dict[str, object], expected: set[str]
) -> None:
    entry = {"api": {"match": "app", **policy}}
    options: dict[str, object] = (
        {"architecture": {"packages": entry}}
        if policy.keys()
        & {"public", "required", "min_imports_to_bundle", "min_importers_to_export"}
        else {"style": {"overrides": entry}}
    )
    assert _active(options) == BASELINE | expected


SHARED_OPTIONS = [
    ({"max_module_imports": 0}, {"SMT110"}),
    ({"imports": {"style": "absolute"}}, {"SMT308"}),
    ({"module_docstrings": "require"}, {"SMT309"}),
    ({"future_annotations": "when-needed"}, {"SMT310", "SMT311"}),
    ({"private_modules": "forbid"}, {"SMT312"}),
    ({"type_dispatch": {}}, {"SMT315"}),
    ({"private_attribute_boundary": "package"}, {"SMT339"}),
    ({"call_argument_kinds": ["await"]}, {"SMT340"}),
    ({"str_enum_values": "auto"}, {"SMT341"}),
    ({"max_positional_parameters": 0}, {"SMT345"}),
    ({"dataclass_options": []}, {"SMT351"}),
    ({"bases": {}}, {"SMT353"}),
    ({"errors": {}}, {"SMT354"}),
    ({"max_comprehension_loops": 2}, {"SMT363"}),
    ({"max_constructor_decisions": 0}, {"SMT364"}),
    ({"max_private_definitions": 0}, {"SMT367"}),
]


@pytest.mark.parametrize(("policy", "expected"), SHARED_OPTIONS)
@pytest.mark.parametrize("level", ["style", "contract"])
def test_a_single_rule_option_activates_its_rule_at_either_level(
    policy: dict[str, object], expected: set[str], level: str
) -> None:
    structure = (
        policy
        if level == "style"
        else {"overrides": {"api": {"match": "app", **policy}}}
    )
    assert _active({"style": structure}) == BASELINE | expected


@pytest.mark.parametrize(
    "policy",
    [
        {"module_import_exemptions": []},
        {"ordering": {"fix": "off"}},
        {"module_docstrings": "allow", "type_dispatch": {"style": "allow"}},
    ],
)
def test_modifiers_and_allow_values_do_not_activate_a_rule(
    policy: dict[str, object],
) -> None:
    assert _active({"style": policy}) == BASELINE


def test_off_wins_over_an_activating_option() -> None:
    assert (
        _active(
            {
                "style": {"max_comprehension_loops": 2},
                "rules": {"complexity/excess-comprehension-loops": "off"},
            }
        )
        == BASELINE
    )


def test_scope_settings_alone_do_not_enable_explicit_heuristics() -> None:
    assert (
        _active({"architecture": {"import_graph": {"cycles": ["siblings"]}}})
        == BASELINE
    )
    assert _active(
        {"rules": {"SMT104": "error", "architecture/unclassified-module": "warning"}}
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
