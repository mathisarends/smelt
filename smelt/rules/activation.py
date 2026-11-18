"""Typed, declarative activation metadata for configuration-driven rules.

An option that belongs to a single rule enables that rule wherever it applies:
set under style it covers the project, set in a package contract it covers
that package. Selecting the rule in rules or with --select enables it everywhere
with the built-in defaults, and an explicit off always wins.
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


def _policies(config: SmeltConfig) -> tuple[PackagePolicy, ...]:
    return (config.structure, *config.structure.contracts.values())


def _option(
    option: PolicyOption,
    description: str,
    accept: Callable[[Any], bool] = lambda value: value is not None,
) -> PolicyActivation:
    """Activation by an option set under style or in any package contract."""
    return PolicyActivation(
        description,
        lambda config: any(
            accept(getattr(policy, option)) for policy in _policies(config)
        ),
        option,
    )


POLICIES = {
    "SMT331": PolicyActivation(
        "style.file_naming selects a canonical YAML extension or Compose basename.",
        lambda config: (
            config.structure.file_naming.yaml_extension is not None
            or config.structure.file_naming.compose_basename is not None
        ),
    ),
    "SMT101": PolicyActivation(
        "architecture.layers defines layer dependencies.",
        lambda config: bool(config.architecture.layers),
    ),
    "SMT102": PolicyActivation(
        "architecture.features defines feature boundaries.",
        lambda config: config.architecture.features is not None,
    ),
    "SMT103": PolicyActivation(
        "A layer's third_party policy restricts dependencies.",
        lambda config: any(
            layer.third_party.default == "deny" or layer.third_party.deny
            for layer in config.architecture.layers.values()
        ),
    ),
    "SMT105": PolicyActivation(
        "architecture.shared or architecture.modules declares shared/central modules.",
        lambda config: bool(config.architecture.shared or config.architecture.modules),
    ),
    "SMT106": PolicyActivation(
        "architecture.composition_root or architecture.wiring declares assembly boundaries.",
        lambda config: bool(
            config.architecture.composition_root or config.architecture.wiring
        ),
    ),
    "SMT107": PolicyActivation(
        "A package contract declares public entry points, including an explicitly empty public list.",
        lambda config: any(
            contract.public is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT108": PolicyActivation(
        "A package contract sets min_imports_to_bundle.",
        lambda config: any(
            contract.min_imports_to_bundle is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT109": PolicyActivation(
        "A package contract sets min_importers_to_export.",
        lambda config: any(
            contract.min_importers_to_export is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT110": _option(
        "max_module_imports",
        "style or a package override sets max_module_imports, including zero.",
    ),
    "SMT111": PolicyActivation(
        "architecture.members declares workspace member boundaries.",
        lambda config: bool(config.architecture.members),
    ),
    "SMT112": PolicyActivation(
        "project.workspace is uv; static member metadata declares dependencies.",
        lambda config: config.project.workspace == "uv",
    ),
    "SMT302": PolicyActivation(
        "style.forbidden_names contains naming restrictions.",
        lambda config: bool(config.structure.forbidden_names),
    ),
    "SMT306": PolicyActivation(
        "project.coverage is strict, including --strict-coverage; the guard cannot be disabled in strict mode.",
        lambda config: config.project.coverage == "strict",
    ),
    "SMT307": PolicyActivation(
        "A package contract requires modules or named public entry points.",
        lambda config: any(
            contract.required or contract.public
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT308": _option("imports", "style or a package override sets imports."),
    "SMT309": _option(
        "module_docstrings",
        "style or a package override sets module_docstrings to require or forbid.",
        lambda value: value in {"forbid", "require"},
    ),
    "SMT310": _option(
        "future_annotations",
        "style or a package override sets future_annotations to forbid or when-needed.",
        lambda value: value in {"forbid", "when-needed"},
    ),
    "SMT311": _option(
        "future_annotations",
        "style or a package override sets future_annotations to when-needed.",
        lambda value: value == "when-needed",
    ),
    "SMT312": _option(
        "private_modules",
        "style or a package override sets private_modules to forbid.",
        lambda value: value == "forbid",
    ),
    "SMT315": _option(
        "type_dispatch",
        "style or a package override sets type_dispatch.style to match.",
        lambda value: value is not None and value.style == "match",
    ),
    "SMT339": _option(
        "private_attribute_boundary",
        "style or a package override sets private_attribute_boundary.",
    ),
    "SMT340": _option(
        "call_argument_kinds",
        "style or a package override sets call_argument_kinds.",
    ),
    "SMT341": _option(
        "str_enum_values",
        "style or a package override sets str_enum_values.",
    ),
    "SMT345": _option(
        "max_positional_parameters",
        "style or a package override sets max_positional_parameters, including zero.",
    ),
    "SMT351": _option(
        "dataclass_options",
        "style or a package override sets dataclass_options, including [].",
    ),
    "SMT353": _option("bases", "style or a package override sets bases, including {}."),
    "SMT354": _option(
        "errors", "style or a package override sets errors, including {}."
    ),
    "SMT363": _option(
        "max_comprehension_loops",
        "style or a package override sets max_comprehension_loops.",
    ),
    "SMT364": _option(
        "max_constructor_decisions",
        "style or a package override sets max_constructor_decisions, including zero.",
    ),
    "SMT367": _option(
        "max_private_definitions",
        "style or a package override sets max_private_definitions, including zero.",
    ),
    "SMT401": PolicyActivation(
        "tests.layout is mirror.", lambda config: config.tests.layout == "mirror"
    ),
    "SMT421": PolicyActivation(
        "tests.forbidden_mock_paths contains test path patterns.",
        lambda config: bool(config.tests.forbidden_mock_paths),
    ),
    "SMT423": PolicyActivation(
        "tests.max_patches_per_test is set, including zero.",
        lambda config: config.tests.max_patches_per_test is not None,
    ),
}
