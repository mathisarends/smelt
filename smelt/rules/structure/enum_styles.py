from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc

_SCOPE = (
    "It considers production classes inheriting directly from enum.StrEnum "
    "(imported as StrEnum, an alias or enum.StrEnum). Member aliases, private "
    "names and tests are ignored."
)


class StrEnumValueConvention(Rule):
    activation = POLICIES["MOD004"]
    code = "MOD004"
    name = "modeling/str-enum-value-convention"
    category = Category.MODELING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A StrEnum spells its values against the configured auto/explicit convention.",
        rationale=(
            "StrEnum values follow one project convention, chosen with "
            "str_enum_values. explicit (the default) keeps every value "
            "visible where it is declared, so renaming a member cannot silently "
            "change a serialized value; a member using auto() is reported. auto "
            "treats auto() as the convention because it derives the lowercased "
            "member name: an explicit string repeating that name is noise and "
            "hides which values are deliberate wire contracts. Under auto, a class "
            "with custom values such as 'mouseMoved' keeps explicit strings for "
            "every member and its auto() members are reported instead. " + _SCOPE
        ),
        bad=(
            "# style: {str_enum_values: explicit}\n"
            "class SignalingMessageType(StrEnum):\n"
            "    DESCRIPTION = auto()\n"
            "    READY = auto()"
        ),
        good=(
            "class SignalingMessageType(StrEnum):\n"
            '    DESCRIPTION = "description"\n'
            '    READY = "ready"'
        ),
        fix=(
            "Under explicit, spell each reported auto() member as its lowercased "
            "name, the value auto() produces for a StrEnum. Under auto, replace "
            "each reported explicit string with auto(), or spell the auto() "
            "members of a class with custom values explicitly. check --fix "
            "rewrites the reported members and adds or removes the auto import; "
            "a class that overrides _generate_next_value_ is left for a manual "
            "edit. Enable rules: "
            "{modeling/str-enum-value-convention: hint}, select MOD004, or set "
            "str_enum_values under style or in a style override. Reasoned "
            "inline suppressions are supported."
        ),
        canonical=(
            "Follow the package's StrEnum value convention (explicit strings or auto())"
        ),
        config=("rules", "style.str_enum_values", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        if _value_style(ctx, target.module) == "auto":
            return "Use auto() for StrEnum values that equal the lowercased member name"
        return 'Spell StrEnum values explicitly (RED = "red"), not with auto()'


def _value_style(ctx: AnalysisContext, module: str) -> str:
    contract = ctx.contracts.policy_for(module, "str_enum_values")
    return (
        contract.spec.str_enum_values
        if contract and contract.spec.str_enum_values
        else POLICY_DEFAULTS.str_enum_values
    )
