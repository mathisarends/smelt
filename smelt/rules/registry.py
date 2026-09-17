from __future__ import annotations

import importlib
import sys
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from smelt.rules.base import Rule, RuleSet
from smelt.rules.code.construction import ConcreteConstruction, ConcreteDependency
from smelt.rules.code.container import ContainerUsage
from smelt.rules.code.inheritance import ForbiddenBaseClass
from smelt.rules.code.roles import MisplacedRole
from smelt.rules.code.self_reference import SelfClassReference
from smelt.rules.dependencies.composition_root import CompositionRootLeak
from smelt.rules.dependencies.cycles import ImportCycle
from smelt.rules.dependencies.features import CrossFeatureImport, SharedImportsFeature
from smelt.rules.dependencies.layers import LayerBoundary
from smelt.rules.dependencies.third_party import ThirdPartyDenied
from smelt.rules.meta import StaleBaseline, SuppressionWithoutReason, UnusedSuppression
from smelt.rules.structure.layout import (
    CrowdedPackage,
    UnclassifiedModule,
    UnknownLayer,
)
from smelt.rules.structure.naming import ForbiddenPackageName
from smelt.rules.structure.roles import RoleFile
from smelt.rules.testing.api import ApiUsedOnlyByTests
from smelt.rules.testing.bloat import BloatedTestChange
from smelt.rules.testing.location import MisplacedTestFile
from smelt.rules.testing.mocks import (
    InteractionAssertion,
    MocksFirstParty,
    TooManyMocks,
)
from smelt.rules.testing.patching import PatchesInternal, PrivateAccess

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

ENTRY_POINT_GROUP = "smelt.rules"


class PluginError(Exception):
    pass


def builtin_rules() -> list[Rule]:
    return [
        LayerBoundary(),
        CrossFeatureImport(),
        ThirdPartyDenied(),
        ImportCycle(),
        SharedImportsFeature(),
        CompositionRootLeak(),
        ConcreteConstruction(),
        ConcreteDependency(),
        ForbiddenBaseClass(),
        MisplacedRole(),
        ContainerUsage(),
        SelfClassReference(),
        UnknownLayer(),
        ForbiddenPackageName(),
        RoleFile(),
        CrowdedPackage(),
        UnclassifiedModule(),
        MisplacedTestFile(),
        PatchesInternal(),
        PrivateAccess(),
        MocksFirstParty(),
        TooManyMocks(),
        InteractionAssertion(),
        BloatedTestChange(),
        ApiUsedOnlyByTests(),
        UnusedSuppression(),
        SuppressionWithoutReason(),
        StaleBaseline(),
    ]


def build_rule_set(
    plugins: Iterable[str] = (),
    *,
    search_paths: Iterable[Path] = (),
    load_entry_points: bool = True,
) -> RuleSet:
    rules = builtin_rules()
    extra: list[Rule] = []
    paths = [str(p) for p in search_paths]
    sys.path[:0] = paths
    try:
        for module_name in plugins:
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:
                msg = f'cannot import plugin "{module_name}": {exc}'
                raise PluginError(msg) from exc
            found = getattr(module, "RULES", None)
            if found is None:
                msg = f'plugin "{module_name}" does not define RULES'
                raise PluginError(msg)
            extra.extend(_instantiate(found, module_name))
    finally:
        for path in paths:
            if path in sys.path:
                sys.path.remove(path)
    if load_entry_points:
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            try:
                loaded = entry.load()
            except Exception as exc:
                msg = f'cannot load entry point "{entry.name}": {exc}'
                raise PluginError(msg) from exc
            extra.extend(_instantiate(loaded, entry.name))

    known = {rule.code for rule in rules}
    for rule in extra:
        if rule.code in known:
            msg = f'plugin rule code "{rule.code}" is already registered'
            raise PluginError(msg)
        known.add(rule.code)
        rules.append(rule)
    return RuleSet(sorted(rules, key=lambda r: r.code))


def _instantiate(value: object, source: str) -> list[Rule]:
    items = value if isinstance(value, list | tuple | set | frozenset) else [value]
    rules: list[Rule] = []
    for item in items:
        candidate = item() if isinstance(item, type) else item
        if not isinstance(candidate, Rule):
            msg = f'"{source}" provided {item!r}, which does not implement the Rule protocol'
            raise PluginError(msg)
        rules.append(candidate)
    return rules
