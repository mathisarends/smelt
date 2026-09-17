from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.analysis.syntax import ClassInfo, SyntaxIndex
    from smelt.config.models import SmeltConfig

# Subclassing a Protocol without listing Protocol again makes a concrete class.
_DIRECT_ONLY_BASES = frozenset({"typing.Protocol", "typing_extensions.Protocol"})


@dataclass(frozen=True, slots=True)
class RoleMatch:
    role: str
    cls: ClassInfo


class RoleIndex:
    def __init__(self, config: SmeltConfig, syntax: SyntaxIndex) -> None:
        self.config = config
        self.syntax = syntax
        self.by_class: dict[str, frozenset[str]] = self._detect()

    def _detect(self) -> dict[str, frozenset[str]]:
        roles: dict[str, set[str]] = defaultdict(set)
        classes = self.syntax.classes
        ancestors = {
            name: self.syntax.ancestors(info) for name, info in classes.items()
        }
        for role_name, role in self.config.roles.items():
            base = role.detect.base
            if base is None:
                continue
            for name, info in classes.items():
                direct = {self.syntax.canonical(b) for b in info.bases if b}
                if base in _DIRECT_ONLY_BASES:
                    if base in direct:
                        roles[name].add(role_name)
                elif base in ancestors[name]:
                    roles[name].add(role_name)

        changed = True
        while changed:
            changed = False
            for role_name, role in self.config.roles.items():
                target = role.detect.implements
                if target is None:
                    continue
                for name in classes:
                    if role_name in roles[name] or target in roles[name]:
                        continue
                    if any(target in roles.get(a, ()) for a in ancestors[name]):
                        roles[name].add(role_name)
                        changed = True
        return {name: frozenset(found) for name, found in roles.items() if found}

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
