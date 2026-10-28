"""Resolve package contracts against the analyzed inventory, without importing code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from smelt.config.patterns import module_matches
from smelt.model import is_within

if TYPE_CHECKING:
    from smelt.analysis.files import FileIndex
    from smelt.config.models import PackageContract


@dataclass(frozen=True)
class ResolvedContract:
    name: str
    package: str
    spec: PackageContract

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

    def style_for(self, module: str) -> ResolvedContract | None:
        return max(
            (
                contract
                for contract in self.resolved
                if contract.spec.imports is not None
                and is_within(module, contract.package)
            ),
            key=lambda contract: len(contract.package),
            default=None,
        )

    def style_brief(self, module: str | None) -> dict[str, Any] | None:
        contract = self.style_for(module) if module else None
        if contract is None or contract.spec.imports is None:
            return None
        return {
            "contract": contract.name,
            "package": contract.package,
            **contract.spec.imports.model_dump(mode="json"),
        }

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
                "required": [
                    contract.qualify(entry) for entry in contract.spec.required
                ],
                "missing": self.missing(contract),
                "public": list(contract.public)
                if contract.public is not None
                else None,
                "missing_public": self.missing_public(contract),
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
                    {"bundle_imports_at": contract.spec.bundle_imports_at}
                    if contract.spec.bundle_imports_at is not None
                    else {}
                ),
            }
            for contract in self.resolved
            if module is None
            or is_within(module, contract.package)
            or is_within(contract.package, module)
        ]
