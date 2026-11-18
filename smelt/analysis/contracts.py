"""Resolve package contracts against the analyzed inventory, without importing code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from smelt.config.errors import ConfigError, ConfigIssue
from smelt.config.models import POLICY_DEFAULTS, PackageContract, PackagePolicy
from smelt.config.patterns import module_matches
from smelt.model import is_within

if TYPE_CHECKING:
    from smelt.analysis.files import FileIndex
    from smelt.config.models import StructureConfig

_PROJECT_POLICY = "style"

type PolicyOption = Literal[
    "imports",
    "module_docstrings",
    "future_annotations",
    "private_modules",
    "private_attribute_boundary",
    "ordering",
    "type_dispatch",
    "max_positional_parameters",
    "max_module_imports",
    "module_import_exemptions",
    "max_comprehension_loops",
    "max_constructor_decisions",
    "max_private_definitions",
    "call_argument_kinds",
    "str_enum_values",
    "dataclass_options",
    "bases",
    "errors",
]


@dataclass(frozen=True, slots=True)
class ResolvedContract:
    name: str
    package: str
    spec: PackageContract

    @property
    def project_wide(self) -> bool:
        """Whether this is the structure-level policy rather than a package contract."""
        return not self.package

    @property
    def origin(self) -> str:
        """How findings name the policy: structure or contract "<name>"."""
        return _PROJECT_POLICY if self.project_wide else f'contract "{self.name}"'

    def qualify(self, relative: str) -> str:
        return self.package if relative == "." else f"{self.package}.{relative}"

    @property
    def public(self) -> tuple[str, ...] | None:
        if self.spec.public is None:
            return None
        return tuple(self.qualify(entry) for entry in self.spec.public)

    def exposes(self, module: str) -> bool:
        public = self.public
        return public is None or any(
            module_matches(pattern, module) for pattern in public
        )


class ContractIndex:
    def __init__(self, files: FileIndex) -> None:
        self.files = files
        self.known = frozenset(files.sources) | frozenset(files.packages)
        self.resolved = tuple(
            ResolvedContract(name, package, spec)
            for name, spec in sorted(files.config.structure.contracts.items())
            for package in sorted(files.packages)
            if module_matches(spec.match, package)
        )
        self.project = _project_policy(files.config.structure)

    def facade_policies(
        self, policy: Literal["min_imports_to_bundle", "min_importers_to_export"]
    ) -> tuple[ResolvedContract, ...]:
        """One threshold per concrete package; more literal match segments win."""
        candidates: dict[str, list[ResolvedContract]] = {}
        for contract in self.resolved:
            if getattr(contract.spec, policy) is not None:
                candidates.setdefault(contract.package, []).append(contract)
        chosen: list[ResolvedContract] = []
        for package, matches in sorted(candidates.items()):
            rank = min(c.spec.match.split(".").count("*") for c in matches)
            winners = [c for c in matches if c.spec.match.split(".").count("*") == rank]
            first = winners[0]
            for other in winners[1:]:
                if getattr(first.spec, policy) != getattr(other.spec, policy) or (
                    policy == "min_importers_to_export"
                    and first.spec.count_tests != other.spec.count_tests
                ):
                    raise ConfigError(
                        [
                            ConfigIssue(
                                f"architecture.packages.{other.name}.{policy}",
                                f"conflicting {policy} policies for {package} with equally specific matches",
                            )
                        ],
                        "smelt configuration",
                    )
            chosen.append(first)
        return tuple(chosen)

    def header_brief(self, module: str | None) -> dict[str, Any]:
        policies: dict[str, Any] = {}
        if module is None:
            return policies
        for key in ("module_docstrings", "future_annotations"):
            contract = self.policy_for(module, key)
            if contract is not None:
                policies[key] = {
                    "contract": contract.name,
                    "package": contract.package or None,
                    "policy": getattr(contract.spec, key),
                }
        return policies

    def naming_brief(self, module: str | None) -> dict[str, Any]:
        contract = self.policy_for(module, "private_modules") if module else None
        if contract is None:
            return {}
        return {
            "private_modules": {
                "contract": contract.name,
                "package": contract.package or None,
                "policy": contract.spec.private_modules,
            }
        }

    def style_brief(self, module: str | None) -> dict[str, Any] | None:
        contract = self.style_for(module) if module else None
        if contract is None or contract.spec.imports is None:
            return None
        return {
            "contract": contract.name,
            "package": contract.package or None,
            **contract.spec.imports.model_dump(mode="json"),
        }

    def style_for(self, module: str) -> ResolvedContract | None:
        return self.policy_for(module, "imports")

    def ordering_brief(self, module: str | None) -> dict[str, Any] | None:
        contract = self.policy_for(module, "ordering") if module else None
        if contract is None or contract.spec.ordering is None:
            return None
        return {
            "contract": contract.name,
            "package": contract.package or None,
            **contract.spec.ordering.model_dump(mode="json", by_alias=True),
        }

    def dispatch_brief(self, module: str | None) -> dict[str, Any] | None:
        contract = self.policy_for(module, "type_dispatch") if module else None
        if contract is None or contract.spec.type_dispatch is None:
            return None
        return {
            "contract": contract.name,
            "package": contract.package or None,
            **contract.spec.type_dispatch.model_dump(mode="json"),
        }

    def threshold(
        self,
        module: str,
        policy: Literal[
            "max_module_imports",
            "max_positional_parameters",
            "max_comprehension_loops",
            "max_constructor_decisions",
            "max_private_definitions",
        ],
    ) -> tuple[int, ResolvedContract | None]:
        """The effective limit and where it is set; None means the built-in default."""
        contract = self.policy_for(module, policy)
        value: int | None = getattr(contract.spec, policy) if contract else None
        if value is None:
            return int(getattr(POLICY_DEFAULTS, policy)), None
        return value, contract

    def policy_for(
        self,
        module: str,
        policy: PolicyOption,
    ) -> ResolvedContract | None:
        """The closest contract setting policy, else structure when it sets it."""
        closest = max(
            (
                contract
                for contract in self.resolved
                if getattr(contract.spec, policy) is not None
                and is_within(module, contract.package)
            ),
            key=lambda contract: len(contract.package),
            default=None,
        )
        if closest is None and getattr(self.project.spec, policy) is not None:
            return self.project
        return closest

    def missing(self, contract: ResolvedContract) -> list[str]:
        return [
            contract.qualify(entry)
            for entry in contract.spec.required
            if contract.qualify(entry) not in self.known
        ]

    def missing_public(self, contract: ResolvedContract) -> list[str]:
        return [
            pattern
            for pattern in contract.public or ()
            if not any(module_matches(pattern, module) for module in self.known)
        ]

    def describe(self, module: str | None = None) -> list[dict[str, Any]]:
        """Contracts containing a target, or below a package/project target."""
        return [
            {
                "name": contract.name,
                "package": contract.package,
                "match": contract.spec.match,
                "rules": dict(contract.spec.rules),
                "required": [
                    contract.qualify(entry) for entry in contract.spec.required
                ],
                "missing": self.missing(contract),
                "public": list(contract.public)
                if contract.public is not None
                else None,
                "missing_public": self.missing_public(contract),
                **(
                    {
                        "type_dispatch": contract.spec.type_dispatch.model_dump(
                            mode="json"
                        )
                    }
                    if contract.spec.type_dispatch
                    else {}
                ),
                **(
                    {"errors": contract.spec.errors.model_dump(mode="json")}
                    if contract.spec.errors
                    else {}
                ),
                **(
                    {
                        "ordering": contract.spec.ordering.model_dump(
                            mode="json", by_alias=True
                        )
                    }
                    if contract.spec.ordering
                    else {}
                ),
                "target_public": contract.exposes(module)
                if module
                and is_within(module, contract.package)
                and contract.public is not None
                else None,
                **(
                    {"imports": contract.spec.imports.model_dump(mode="json")}
                    if contract.spec.imports
                    else {}
                ),
                **(
                    {"min_imports_to_bundle": contract.spec.min_imports_to_bundle}
                    if contract.spec.min_imports_to_bundle is not None
                    else {}
                ),
                **(
                    {
                        "min_importers_to_export": contract.spec.min_importers_to_export,
                        "count_tests": contract.spec.count_tests,
                    }
                    if contract.spec.min_importers_to_export is not None
                    else {}
                ),
                **(
                    {
                        key: getattr(contract.spec, key)
                        for key in (
                            "module_docstrings",
                            "future_annotations",
                            "private_modules",
                            "private_attribute_boundary",
                            "max_positional_parameters",
                            "max_module_imports",
                            "module_import_exemptions",
                            "max_comprehension_loops",
                            "max_constructor_decisions",
                            "max_private_definitions",
                            "call_argument_kinds",
                            "str_enum_values",
                            "dataclass_options",
                            "bases",
                        )
                        if getattr(contract.spec, key) is not None
                    }
                ),
            }
            for contract in self.resolved
            if module is None
            or is_within(module, contract.package)
            or is_within(contract.package, module)
        ]


def _project_policy(structure: StructureConfig) -> ResolvedContract:
    """structure-level options as a contract that every package inherits."""
    options = {name: getattr(structure, name) for name in PackagePolicy.model_fields}
    spec = PackageContract.model_construct(match="*", **options)
    return ResolvedContract(_PROJECT_POLICY, "", spec)
