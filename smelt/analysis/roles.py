from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.analysis.syntax import ClassInfo, SyntaxIndex
    from smelt.analysis.types import TypeIndex
    from smelt.config.models import SmeltConfig

# Subclassing a Protocol without listing Protocol again makes a concrete class.
_DIRECT_ONLY_BASES = frozenset({"typing.Protocol", "typing_extensions.Protocol"})


@dataclass(frozen=True, slots=True)
class RoleMatch:
    role: str
    cls: ClassInfo


class RoleIndex:
    def __init__(
        self,
        config: SmeltConfig,
        syntax: SyntaxIndex,
        types: TypeIndex | None = None,
    ) -> None:
        self.config = config
        self.syntax = syntax
        self.types = types
        self.by_class: dict[str, frozenset[str]] = self._detect()

    def _detect(self) -> dict[str, frozenset[str]]:
        roles: dict[str, set[str]] = defaultdict(set)
        ancestors = {
            name: self.syntax.ancestors(info)
            for name, info in self.syntax.classes.items()
        }
        self._detect_by_base(roles, ancestors)
        self._detect_by_ancestry(roles, ancestors)
        if self.types is not None:
            self._detect_structurally(roles)
        return {name: frozenset(found) for name, found in roles.items() if found}

    def _detect_by_base(
        self, roles: dict[str, set[str]], ancestors: dict[str, list[str]]
    ) -> None:
        for role_name, role in self.config.roles.items():
            base = role.detect.base
            if base is None:
                continue
            for name, info in self.syntax.classes.items():
                direct = {self.syntax.canonical(b) for b in info.bases if b}
                wanted = direct if base in _DIRECT_ONLY_BASES else ancestors[name]
                if base in wanted:
                    roles[name].add(role_name)

    def _detect_by_ancestry(
        self, roles: dict[str, set[str]], ancestors: dict[str, list[str]]
    ) -> None:
        """Propagate ``implements`` roles down the inheritance chains, until stable."""
        changed = True
        while changed:
            changed = False
            for role_name, target in self._implement_roles():
                for name in self.syntax.classes:
                    if {role_name, target} & roles[name]:
                        continue
                    if any(target in roles.get(a, ()) for a in ancestors[name]):
                        roles[name].add(role_name)
                        changed = True

    def _implement_roles(self) -> list[tuple[str, str]]:
        return [
            (name, role.detect.implements)
            for name, role in self.config.roles.items()
            if role.detect.implements is not None
        ]

    def _detect_structurally(self, roles: dict[str, set[str]]) -> None:
        """With type information, a class can implement a port without inheriting it."""
        types = self.types
        if types is None:  # pragma: no cover - guarded by the caller
            return
        for role_name, target in self._implement_roles():
            ports = sorted(name for name, found in roles.items() if target in found)
            candidates = [
                name
                for name in self.syntax.classes
                if not {role_name, target} & roles[name]
            ]
            pairs = [(name, port) for name in candidates for port in ports]
            types.prepare(pairs)
            for name, port in pairs:
                if types.implements(name, port):
                    roles[name].add(role_name)

    def roles_of(self, qualname: str) -> frozenset[str]:
        return self.by_class.get(qualname, frozenset())

    def classes_with(self, role: str) -> list[ClassInfo]:
        classes = self.syntax.classes
        return [
            classes[name]
            for name, roles in sorted(self.by_class.items())
            if role in roles
        ]

    def matches(self) -> list[RoleMatch]:
        classes = self.syntax.classes
        return [
            RoleMatch(role, classes[name])
            for name, roles in sorted(self.by_class.items())
            for role in sorted(roles)
        ]

    def module_roles(self) -> dict[str, frozenset[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for match in self.matches():
            result[match.cls.module].add(match.role)
        return {module: frozenset(roles) for module, roles in result.items()}
