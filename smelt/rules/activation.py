"""Typed, declarative activation metadata for configuration-driven rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.config.models import SmeltConfig


@dataclass(frozen=True, slots=True)
class PolicyActivation:
    description: str
    matches: Callable[[SmeltConfig], bool]


POLICIES = {
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
        "A package contract sets bundle_imports_at.",
        lambda config: any(
            contract.bundle_imports_at is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT302": PolicyActivation(
        "structure.forbidden_names contains naming restrictions.",
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
    "SMT308": PolicyActivation(
        "A package contract declares an imports style.",
        lambda config: any(
            contract.imports is not None
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT309": PolicyActivation(
        "A package contract forbids module docstrings.",
        lambda config: any(
            contract.module_docstrings == "forbid"
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT310": PolicyActivation(
        "A package contract sets future_annotations to forbid or when-needed.",
        lambda config: any(
            contract.future_annotations in {"forbid", "when-needed"}
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT311": PolicyActivation(
        "A package contract sets future_annotations to when-needed.",
        lambda config: any(
            contract.future_annotations == "when-needed"
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT312": PolicyActivation(
        "A package contract forbids private module names.",
        lambda config: any(
            contract.private_modules == "forbid"
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT315": PolicyActivation(
        "A package contract sets type_dispatch.style to match.",
        lambda config: any(
            contract.type_dispatch is not None
            and contract.type_dispatch.style == "match"
            for contract in config.structure.contracts.values()
        ),
    ),
    "SMT401": PolicyActivation(
        "tests.layout is mirror.", lambda config: config.tests.layout == "mirror"
    ),
    "SMT421": PolicyActivation(
        "tests.forbid_mocks_in contains test path patterns.",
        lambda config: bool(config.tests.forbid_mocks_in),
    ),
    "SMT423": PolicyActivation(
        "tests.max_patches_per_test is set, including zero.",
        lambda config: config.tests.max_patches_per_test is not None,
    ),
}
