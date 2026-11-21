"""Typed, declarative policy metadata for configuration-driven rules.

A policy never enables a rule: only a rules entry or --select does. The metadata
names the policy a rule reads. A required policy must exist once the rule is
enabled, and a declared policy whose rule is named nowhere is reported, so a
configured option cannot silently go unchecked. An option set in a style
override limits the rule to that package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.analysis.contracts import PolicyOption
    from smelt.config.models import PackagePolicy, SmeltConfig


@dataclass(frozen=True, slots=True)
class PolicyActivation:
    description: str
    matches: Callable[[SmeltConfig], bool]
    option: PolicyOption | None = None
    """The structure or contract option whose scope limits the activation."""
    required: bool = True
    """False when built-in defaults let the rule run without its policy."""
    accept: Callable[[Any], bool] | None = None
    """Which values of ``option`` declare the policy."""


def _policies(config: SmeltConfig) -> tuple[PackagePolicy, ...]:
    return (config.structure, *config.structure.contracts.values())


def _option(
    option: PolicyOption,
    description: str,
    accept: Callable[[Any], bool] = lambda value: value is not None,
    *,
    required: bool = True,
) -> PolicyActivation:
    """A policy option set under style or in any style override."""
    return PolicyActivation(
        description,
        lambda config: any(
            accept(getattr(policy, option)) for policy in _policies(config)
        ),
        option,
        required,
        accept,
    )


POLICIES = {
    "LAY007": PolicyActivation(
        "style.file_naming selects a canonical YAML extension or Compose basename.",
        lambda config: (
            config.structure.file_naming.yaml_extension is not None
            or config.structure.file_naming.compose_basename is not None
        ),
    ),
    "ARC001": PolicyActivation(
        "architecture.layers defines layer dependencies.",
        lambda config: bool(config.architecture.layers),
    ),
    "ARC002": PolicyActivation(
        "architecture.features defines feature boundaries.",
        lambda config: config.architecture.features is not None,
    ),
    "ARC003": PolicyActivation(
        "A layer's third_party policy restricts dependencies.",
        lambda config: any(
            layer.third_party.default == "deny" or layer.third_party.deny
            for layer in config.architecture.layers.values()
        ),
    ),
    "ARC005": PolicyActivation(
        "architecture.shared or a layer module binding declares shared/central modules.",
        lambda config: bool(
            config.architecture.shared or config.architecture.has_module_bindings
        ),
    ),
    "ARC006": PolicyActivation(
        "architecture.composition_root or architecture.wiring declares assembly boundaries.",
        lambda config: bool(
            config.architecture.composition_root or config.architecture.wiring
        ),
    ),
    "ARC007": PolicyActivation(
        "A package API declares public entry points, including an explicitly empty public list.",
        lambda config: any(
            contract.public is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "IMP001": PolicyActivation(
        "A package API sets min_imports_to_bundle.",
        lambda config: any(
            contract.min_imports_to_bundle is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "ARC008": PolicyActivation(
        "A package API sets min_importers_to_export.",
        lambda config: any(
            contract.min_importers_to_export is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "IMP002": _option(
        "module_import_exemptions",
        "style or a package override sets module_import_exemptions.",
        required=False,
    ),
    "ARC009": PolicyActivation(
        "architecture.members declares workspace member boundaries.",
        lambda config: bool(config.architecture.members),
    ),
    "ARC010": PolicyActivation(
        "project.workspace is uv; static member metadata declares dependencies.",
        lambda config: config.project.workspace == "uv",
    ),
    "LAY001": PolicyActivation(
        "style.forbidden_names contains naming restrictions.",
        lambda config: bool(config.structure.forbidden_names),
    ),
    "ARC014": PolicyActivation(
        "A package API requires modules or named public entry points.",
        lambda config: any(
            contract.required or contract.public
            for contract in config.structure.contracts.values()
        ),
    ),
    "IMP003": _option("imports", "style or a package override sets imports."),
    "LAY002": _option(
        "module_docstrings",
        "style or a package override sets module_docstrings to require or forbid.",
        lambda value: value in {"forbid", "require"},
    ),
    "TYP001": _option(
        "future_annotations",
        "style or a package override sets future_annotations to forbid or when-needed.",
        lambda value: value in {"forbid", "when-needed"},
    ),
    "TYP002": _option(
        "future_annotations",
        "style or a package override sets future_annotations to when-needed.",
        lambda value: value == "when-needed",
    ),
    "LAY003": _option(
        "private_modules",
        "style or a package override sets private_modules to forbid.",
        lambda value: value == "forbid",
    ),
    "IDM002": _option(
        "type_dispatch",
        "style or a package override sets type_dispatch.style to match.",
        lambda value: value is not None and value.style == "match",
    ),
    "VIS004": _option(
        "public_attribute_boundary",
        "style or a package override sets public_attribute_boundary.",
        required=False,
    ),
    "VIS005": _option(
        "private_attribute_boundary",
        "style or a package override sets private_attribute_boundary.",
        required=False,
    ),
    "CPX002": _option(
        "call_argument_kinds",
        "style or a package override sets call_argument_kinds.",
        required=False,
    ),
    "MOD004": _option(
        "str_enum_values",
        "style or a package override sets str_enum_values.",
        required=False,
    ),
    "MOD006": _option(
        "dataclass_options",
        "style or a package override sets dataclass_options, including [].",
    ),
    "IMP004": _option(
        "third_party_reexports",
        "style or a package override lists third_party_reexports.",
        bool,
        required=False,
    ),
    "MOD007": _option("bases", "style or a package override sets bases, including {}."),
    "MOD008": _option(
        "errors", "style or a package override sets errors, including {}."
    ),
    "VIS008": _option(
        "reexports",
        "style or a package override sets reexports to all or alias.",
        lambda value: value in {"all", "alias"},
        required=False,
    ),
    "TST001": PolicyActivation(
        "tests.layout is mirror.", lambda config: config.tests.layout == "mirror"
    ),
    "TST003": PolicyActivation(
        "tests.forbidden_mock_paths contains test path patterns.",
        lambda config: bool(config.tests.forbidden_mock_paths),
    ),
    "TST005": PolicyActivation(
        "The tests/excess-patches entry sets max, including zero.",
        lambda config: config.max_patches_per_test is not None,
    ),
}
