from smelt.rules.base import Rule, RuleSet
from smelt.rules.dependencies.composition_root import CompositionRootLeak
from smelt.rules.dependencies.cycles import ImportCycle
from smelt.rules.dependencies.fanout import PackageImportFanout
from smelt.rules.dependencies.features import CrossFeatureImport, SharedImportsFeature
from smelt.rules.dependencies.interfaces import PrivateModuleImport
from smelt.rules.dependencies.layers import LayerBoundary
from smelt.rules.dependencies.third_party import ThirdPartyDenied
from smelt.rules.meta import ResolvedDebt, SuppressionWithoutReason, UnusedSuppression
from smelt.rules.structure.contracts import MissingRequiredModule
from smelt.rules.structure.coverage import IncompleteCoverage
from smelt.rules.structure.headers import (
    ForbiddenFutureAnnotations,
    ForbiddenModuleDocstring,
    UnnecessaryFutureAnnotations,
)
from smelt.rules.structure.import_style import ImportStyle
from smelt.rules.structure.layout import UnclassifiedModule, UnknownLayer
from smelt.rules.structure.naming import ForbiddenPackageName, NoPrivateModules
from smelt.rules.structure.ordering import DefinitionOrder
from smelt.rules.testing.location import MisplacedTestFile


def builtin_rules() -> list[Rule]:
    return [
        LayerBoundary(),
        CrossFeatureImport(),
        ThirdPartyDenied(),
        ImportCycle(),
        SharedImportsFeature(),
        CompositionRootLeak(),
        PrivateModuleImport(),
        PackageImportFanout(),
        UnknownLayer(),
        ForbiddenPackageName(),
        UnclassifiedModule(),
        IncompleteCoverage(),
        MissingRequiredModule(),
        ImportStyle(),
        ForbiddenModuleDocstring(),
        ForbiddenFutureAnnotations(),
        UnnecessaryFutureAnnotations(),
        NoPrivateModules(),
        DefinitionOrder(),
        MisplacedTestFile(),
        UnusedSuppression(),
        SuppressionWithoutReason(),
        ResolvedDebt(),
    ]


def build_rule_set() -> RuleSet:
    return RuleSet(sorted(builtin_rules(), key=lambda r: r.code))
