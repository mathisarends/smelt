from smelt.rules.base import Rule, RuleSet
from smelt.rules.dependencies.composition_root import CompositionRootLeak
from smelt.rules.dependencies.cycles import ImportCycle
from smelt.rules.dependencies.features import CrossFeatureImport, SharedImportsFeature
from smelt.rules.dependencies.layers import LayerBoundary
from smelt.rules.dependencies.third_party import ThirdPartyDenied
from smelt.rules.meta import ResolvedDebt, SuppressionWithoutReason, UnusedSuppression
from smelt.rules.structure.layout import UnclassifiedModule, UnknownLayer
from smelt.rules.structure.naming import ForbiddenPackageName
from smelt.rules.testing.location import MisplacedTestFile


def builtin_rules() -> list[Rule]:
    return [
        LayerBoundary(),
        CrossFeatureImport(),
        ThirdPartyDenied(),
        ImportCycle(),
        SharedImportsFeature(),
        CompositionRootLeak(),
        UnknownLayer(),
        ForbiddenPackageName(),
        UnclassifiedModule(),
        MisplacedTestFile(),
        UnusedSuppression(),
        SuppressionWithoutReason(),
        ResolvedDebt(),
    ]


def build_rule_set() -> RuleSet:
    return RuleSet(sorted(builtin_rules(), key=lambda r: r.code))
