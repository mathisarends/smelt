from pathlib import Path

import pytest

from smelt.config import ConfigError
from smelt.config.loader import LoadedConfig
from smelt.config.models import SmeltConfig
from smelt.engine.check import (
    CheckOptions,
    InvalidOptionError,
    _resolve_active_rules,  # smelt: ignore[TST002] -- Exercise the private analysis primitive and its safety boundaries directly.
    rule_meta,
)
from smelt.engine.rule_settings import RuleSettings
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import Rule
from smelt.rules.registry import build_rule_set

BASELINE = {"ARC011", "MET001", "MET002", "MET003", "MET004", "MET005"}


def _loaded(extra: dict[str, object]) -> LoadedConfig:
    config = SmeltConfig.model_validate(
        {"version": 2, "project": {"root_packages": ["app"]}, **extra}
    )
    return LoadedConfig(config, Path("smelt.yml"))


def _active(extra: dict[str, object], options: CheckOptions | None = None) -> set[str]:
    return {
        rule.code
        for rule, _ in _resolve_active_rules(
            build_rule_set(), _loaded(extra), options or CheckOptions()
        )
    }


def _checked(extra: dict[str, object]) -> tuple[set[str], set[str]]:
    """The enabled non-baseline rules and the rules of unnamed declared policies."""
    unused = RuleSettings(build_rule_set(), _loaded(extra)).policy_enablements()
    return _active(extra) - BASELINE, {rule.code for rule, _ in unused}


def _expected(extra: dict[str, object], codes: set[str]) -> tuple[set[str], set[str]]:
    """Rules entries enable rules; a policy alone only reports its unnamed rule."""
    return (codes, set()) if "rules" in extra else (set(), codes)


def test_minimal_configuration_runs_only_the_small_baseline() -> None:
    assert _active({}) == BASELINE
    assert not Rule.enabled_by_default
    assert Rule.activation is None


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"architecture": {"layers": {"domain": {"path": "domain"}}}}, {"ARC001"}),
        ({"architecture": {"features": {"root": "app.features"}}}, {"ARC002"}),
        (
            {
                "architecture": {
                    "layers": {"domain": {"path": "domain", "third_party": "deny"}}
                }
            },
            {"ARC001", "ARC003"},
        ),
        ({"architecture": {"shared": ["app.shared"]}}, {"ARC005"}),
        (
            {
                "architecture": {
                    "layers": {"infrastructure": {"modules": ["app.platform"]}}
                }
            },
            {"ARC001", "ARC005"},
        ),
        ({"architecture": {"composition_root": ["app.main"]}}, {"ARC006"}),
        ({"architecture": {"wiring": ["app.di"]}}, {"ARC006"}),
        ({"style": {"forbidden_names": ["utils"]}}, {"LAY001"}),
        ({"style": {"module_import_exemptions": []}}, {"IMP002"}),
        ({"style": {"file_naming": {"yaml_extension": "yaml"}}}, {"LAY007"}),
        ({"style": {"file_naming": {"compose_basename": "compose"}}}, {"LAY007"}),
        (
            {"project": {"workspace": "uv", "workspace_layout": "src"}},
            {"ARC010", "LAY013"},
        ),
        ({"rules": {"architecture/uncovered-source": "error"}}, {"ARC013"}),
        ({"tests": {"layout": "mirror"}}, {"TST001"}),
        ({"rules": {"tests/private-access": "error"}}, {"TST002"}),
        ({"tests": {"forbidden_mock_paths": ["tests/domain/**"]}}, {"TST003"}),
        ({"rules": {"tests/string-patch-target": "error"}}, {"TST004"}),
        (
            {"rules": {"tests/excess-patches": {"severity": "error", "max": 0}}},
            {"TST005"},
        ),
        ({"rules": {"tests/missing-outcome-check": "warning"}}, {"TST006"}),
        ({"rules": {"tests/vacuous-assertion": "warning"}}, {"TST007"}),
        ({"rules": {"tests/unbounded-wait": "warning"}}, {"TST008"}),
        ({"rules": {"tests/independent-case-loop": "hint"}}, {"TST009"}),
        ({"rules": {"tests/missing-await-assertion": "warning"}}, {"TST010"}),
        ({"rules": {"visibility/test-only-public-function": "warning"}}, {"VIS009"}),
        ({"rules": {"visibility/test-only-public-attribute": "warning"}}, {"VIS010"}),
        ({"rules": {"complexity/excess-attribute-depth": "hint"}}, {"CPX001"}),
    ],
)
def test_a_project_policy_needs_its_named_rule(
    extra: dict[str, object], expected: set[str]
) -> None:
    assert _checked(extra) == _expected(extra, expected)


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({"public": []}, {"ARC007"}),
        ({"public": ["."]}, {"ARC007", "ARC014"}),
        ({"required": ["api"]}, {"ARC014"}),
        ({"min_imports_to_bundle": 3}, {"IMP001"}),
        ({"module_import_exemptions": ["facades"]}, {"IMP002"}),
        ({"min_importers_to_export": 3}, {"ARC008"}),
        ({"imports": {"style": "absolute"}}, {"IMP003"}),
        ({"module_docstrings": "forbid"}, {"LAY002"}),
        ({"future_annotations": "forbid"}, {"TYP001"}),
        ({"future_annotations": "when-needed"}, {"TYP001", "TYP002"}),
        ({"private_modules": "forbid"}, {"LAY003"}),
        ({"ordering": {}, "rules": {"layout/definition-order": "error"}}, {"LAY004"}),
        ({"rules": {"idioms/bundled-instance-assignment": "error"}}, {"IDM001"}),
        ({"type_dispatch": {}}, {"IDM002"}),
        ({"rules": {"visibility/internal-public-member": "warning"}}, {"VIS001"}),
        ({"rules": {"layout/misplaced-static-helper": "hint"}}, {"LAY005"}),
        ({"rules": {"complexity/excess-attribute-depth": "hint"}}, {"CPX001"}),
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
def test_a_package_policy_needs_its_named_rule(
    policy: dict[str, object], expected: set[str]
) -> None:
    entry = {"api": {"match": "app", **policy}}
    options: dict[str, object] = (
        {"architecture": {"packages": entry}}
        if policy.keys()
        & {"public", "required", "min_imports_to_bundle", "min_importers_to_export"}
        else {"style": {"overrides": entry}}
    )
    assert _checked(options) == _expected(policy, expected)


SHARED_OPTIONS = [
    ({"module_import_exemptions": []}, {"IMP002"}),
    ({"imports": {"style": "absolute"}}, {"IMP003"}),
    ({"module_docstrings": "require"}, {"LAY002"}),
    ({"future_annotations": "when-needed"}, {"TYP001", "TYP002"}),
    ({"private_modules": "forbid"}, {"LAY003"}),
    ({"type_dispatch": {}}, {"IDM002"}),
    ({"public_attribute_boundary": "module"}, {"VIS004"}),
    ({"private_attribute_boundary": "package"}, {"VIS005"}),
    ({"navigation": {}}, {"CPX001"}),
    ({"call_argument_kinds": ["await"]}, {"CPX002"}),
    ({"str_enum_values": "auto"}, {"MOD004"}),
    ({"dataclass_options": []}, {"MOD006"}),
    ({"bases": {}}, {"MOD007"}),
    ({"errors": {}}, {"MOD008"}),
    ({"reexports": "all"}, {"VIS008"}),
    ({"reexports": "alias"}, {"VIS008"}),
]


@pytest.mark.parametrize(("policy", "expected"), SHARED_OPTIONS)
@pytest.mark.parametrize("level", ["style", "contract"])
def test_a_single_rule_option_is_reported_until_its_rule_is_named(
    policy: dict[str, object], expected: set[str], level: str
) -> None:
    structure = (
        policy
        if level == "style"
        else {"overrides": {"api": {"match": "app", **policy}}}
    )
    assert _checked({"style": structure}) == (set(), expected)


@pytest.mark.parametrize(
    "policy",
    [
        {"ordering": {"fix": "off"}},
        {"module_docstrings": "allow", "type_dispatch": {"style": "allow"}},
        {"reexports": "allow"},
    ],
)
def test_modifiers_and_allow_values_declare_no_policy(
    policy: dict[str, object],
) -> None:
    assert _checked({"style": policy}) == (set(), set())


def test_off_keeps_a_declared_policy_unchecked_without_a_warning() -> None:
    assert _checked(
        {
            "style": {"module_import_exemptions": []},
            "rules": {"imports/excess-module-imports": "off"},
        }
    ) == (set(), set())


def test_a_broad_off_acknowledges_a_declared_policy() -> None:
    assert _checked(
        {
            "architecture": {"layers": {"domain": {"path": "domain"}}},
            "rules": {"architecture": "off"},
        }
    ) == (set(), set())


def test_a_named_rule_runs_with_its_declared_policy() -> None:
    assert _checked(
        {
            "architecture": {"layers": {"domain": {"path": "domain"}}},
            "rules": {"architecture/layer-boundary": "error"},
        }
    ) == ({"ARC001"}, set())


@pytest.mark.parametrize(
    "rule", ["architecture/layer-boundary", "IMP003", "tests/misplaced-test"]
)
def test_a_rule_without_its_required_policy_is_a_config_error(rule: str) -> None:
    with pytest.raises(ConfigError, match="policy is missing") as caught:
        _active({"rules": {rule: "error"}})

    assert caught.value.issues[0].path == f"rules.{rule}"


def test_a_rule_with_built_in_defaults_needs_no_policy() -> None:
    assert _checked({"rules": {"complexity/excess-positional-parameters": "on"}}) == (
        {"CPX003"},
        set(),
    )


def test_an_override_option_is_enabled_in_that_override() -> None:
    settings = RuleSettings(
        build_rule_set(),
        _loaded(
            {
                "style": {
                    "overrides": {
                        "api": {"match": "app", "module_import_exemptions": []}
                    }
                }
            }
        ),
    )

    [(rule, locations)] = settings.policy_enablements()

    assert rule.code == "IMP002"
    assert locations == (("style", "overrides", "api", "rules"),)


def test_scope_settings_alone_do_not_enable_explicit_heuristics() -> None:
    assert (
        _active({"architecture": {"import_graph": {"cycles": ["siblings"]}}})
        == BASELINE
    )
    assert _active(
        {"rules": {"ARC004": "error", "architecture/unclassified-module": "warning"}}
    ) == BASELINE | {"ARC004", "ARC012"}


def test_prefix_selection_enables_opt_in_rules_and_replaces_normal_selection() -> None:
    expected = {
        rule.code for rule in build_rule_set().rules if rule.code.startswith("VIS")
    }
    assert _active({}, CheckOptions(select=("vis",))) == expected


def test_retired_smt_prefix_is_rejected_with_the_upgrade_hint() -> None:
    with pytest.raises(InvalidOptionError, match="SMT codes were replaced"):
        _active({}, CheckOptions(select=("smt3",)))


def test_all_selects_every_supported_rule() -> None:
    assert _active({}, CheckOptions(select=("ALL",))) == {
        rule.code for rule in build_rule_set().rules
    }


def test_off_and_ignore_win_over_explicit_selection() -> None:
    active = _active(
        {"rules": {"ARC004": "off"}}, CheckOptions(select=("ALL",), ignore=("LAY",))
    )
    assert "ARC004" not in active
    assert not any(code.startswith("LAY") for code in active)
    assert _active({}, CheckOptions(ignore=("ALL",))) == set()


def test_ignore_all_cannot_silence_the_coverage_guard() -> None:
    with pytest.raises(InvalidOptionError, match="strict coverage"):
        _active(
            {"rules": {"architecture/uncovered-source": "error"}},
            CheckOptions(ignore=("ALL",)),
        )


@pytest.mark.parametrize("value", ["warning", "hint"])
def test_the_coverage_guard_only_reports_errors(value: str) -> None:
    with pytest.raises(ConfigError, match="always reports errors"):
        _active({"rules": {"architecture/uncovered-source": value}})


def test_a_broad_off_leaves_the_coverage_guard_enabled() -> None:
    assert "ARC013" in _active(
        {"rules": {"architecture/uncovered-source": "error", "ALL": "off"}}
    )


def test_strict_coverage_still_runs_outside_a_select_prefix() -> None:
    assert "ARC013" in _active(
        {"rules": {"architecture/uncovered-source": "error"}},
        CheckOptions(select=("ARC",)),
    )


def test_catalog_exposes_every_rule_activation_decision() -> None:
    modes = {
        rule.code: rule_meta(rule).activation["mode"] for rule in build_rule_set().rules
    }
    assert {code for code, mode in modes.items() if mode == "default"} == BASELINE
    assert set(modes.values()) == {"default", "explicit"}
    for rule in build_rule_set().rules:
        activation = getattr(rule, "activation", None)
        if activation is not None:
            assert isinstance(activation, PolicyActivation)
            assert activation.description.strip()
            assert not rule_meta(rule).enabled_by_default
