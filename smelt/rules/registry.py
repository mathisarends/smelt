"""Explicit rule registration, with generated lightweight selection metadata."""

from __future__ import annotations

import json
from functools import cached_property
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import RuleSet

if TYPE_CHECKING:
    from collections.abc import Iterable

    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.violation import Violation
    from smelt.rules.base import Rule, RuleDoc

_IMPLEMENTATIONS = (
    "smelt.rules.dependencies.layers:LayerBoundary",
    "smelt.rules.dependencies.features:CrossFeatureImport",
    "smelt.rules.dependencies.third_party:ThirdPartyDenied",
    "smelt.rules.dependencies.cycles:ImportCycle",
    "smelt.rules.dependencies.features:SharedImportsFeature",
    "smelt.rules.dependencies.composition_root:CompositionRootLeak",
    "smelt.rules.dependencies.interfaces:PrivateModuleImport",
    "smelt.rules.dependencies.fanout:PackageImportFanout",
    "smelt.rules.structure.layout:UnknownLayer",
    "smelt.rules.structure.naming:ForbiddenPackageName",
    "smelt.rules.structure.layout:UnclassifiedModule",
    "smelt.rules.structure.coverage:IncompleteCoverage",
    "smelt.rules.structure.contracts:MissingRequiredModule",
    "smelt.rules.structure.import_style:ImportStyle",
    "smelt.rules.structure.headers:ForbiddenModuleDocstring",
    "smelt.rules.structure.headers:ForbiddenFutureAnnotations",
    "smelt.rules.structure.headers:UnnecessaryFutureAnnotations",
    "smelt.rules.structure.naming:NoPrivateModules",
    "smelt.rules.structure.ordering:PublicDefinitionsFirst",
    "smelt.rules.structure.assignments:BundledInstanceAssignment",
    "smelt.rules.structure.type_dispatch:PreferMatchTypeDispatch",
    "smelt.rules.structure.members:InternalPublicMember",
    "smelt.rules.structure.members:InternalStaticHelper",
    "smelt.rules.structure.enum_values:RedundantStrEnumValue",
    "smelt.rules.structure.package_imports:PreferPackageImport",
    "smelt.rules.testing.location:MisplacedTestFile",
    "smelt.rules.testing.private_access:PrivateTestAccess",
    "smelt.rules.testing.mocking:ForbiddenTestMock",
    "smelt.rules.testing.mocking:StringPatchTarget",
    "smelt.rules.testing.mocking:ExcessiveTestPatches",
    "smelt.rules.testing.outcomes:TestWithoutOutcomeCheck",
    "smelt.rules.testing.outcomes:TestOnlyChecksPossiblyEmptyData",
    "smelt.rules.testing.timing:UnboundedTestWait",
    "smelt.rules.testing.cases:IndependentTestCaseLoop",
    "smelt.rules.testing.awaits:AsyncMockWithoutAwaitCheck",
    "smelt.rules.testing.public_functions:TestOnlyPublicFunction",
    "smelt.rules.testing.public_attributes:TestOnlyPublicAttribute",
    "smelt.rules.meta:UnusedSuppression",
    "smelt.rules.meta:SuppressionWithoutReason",
    "smelt.rules.meta:ResolvedDebt",
)


def _load(target: str) -> Rule:
    module, name = target.split(":")
    return cast("Rule", getattr(import_module(module), name)())


class _LazyRule:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.code = str(metadata["code"])
        self.name = str(metadata["name"])
        self.category = Category(metadata["category"])
        self.default_severity = Severity(metadata["default_severity"])
        self.requires = frozenset(Index(value) for value in metadata["requires"])
        self.scope = str(metadata["scope"])
        self.enabled_by_default = bool(metadata["enabled_by_default"])
        self.activation = POLICIES.get(self.code)
        self.file_local = bool(metadata["file_local"])
        self.test_local = bool(metadata["test_local"])
        self.member_local = bool(metadata["member_local"])
        self._target = str(metadata["target"])

    @cached_property
    def _implementation(self) -> Rule:
        return _load(self._target)

    def check(self, ctx: AnalysisContext) -> Iterable[Violation]:
        return self._implementation.check(ctx)

    def explain(self) -> RuleDoc:
        return self._implementation.explain()


def catalog_metadata() -> list[dict[str, Any]]:
    return [
        {
            "target": f"{type(rule).__module__}:{type(rule).__name__}",
            "code": rule.code,
            "name": rule.name,
            "category": rule.category.value,
            "default_severity": rule.default_severity.value,
            "requires": sorted(index.value for index in rule.requires),
            **{
                flag: getattr(rule, flag, False)
                for flag in (
                    "enabled_by_default",
                    "file_local",
                    "test_local",
                    "member_local",
                )
            },
            "scope": getattr(rule, "scope", "source"),
        }
        for rule in sorted(_builtin_rules(), key=lambda rule: rule.code)
    ]


def build_rule_set(*, lazy: bool = False) -> RuleSet:
    if lazy:
        entries = json.loads(
            Path(__file__).with_name("catalog.json").read_text(encoding="utf-8")
        )
        return RuleSet([_LazyRule(entry) for entry in entries])
    return RuleSet(sorted(_builtin_rules(), key=lambda rule: rule.code))


def _builtin_rules() -> list[Rule]:
    return [_load(target) for target in _IMPLEMENTATIONS]
