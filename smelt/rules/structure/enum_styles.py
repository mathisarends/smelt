from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile

_SCOPE = (
    "It considers production classes inheriting directly from enum.StrEnum "
    "(imported as StrEnum, an alias or enum.StrEnum). Member aliases, private "
    "names and tests are ignored."
)


class StrEnumValueConvention(Rule):
    file_local = True
    activation = POLICIES["SMT341"]
    code = "SMT341"
    name = "modeling/str-enum-value-convention"
    category = Category.MODELING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
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
            "# structure: {str_enum_values: explicit}\n"
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
            "members of a class with custom values explicitly. No automatic "
            "rewrite is performed. Enable rules: "
            "{modeling/str-enum-value-convention: hint}, select SMT341, or set "
            "str_enum_values under structure or in a package contract. Reasoned "
            "inline suppressions are supported."
        ),
        config=("rules", "structure.str_enum_values", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for enum in _str_enums(ctx):
            contract = ctx.contracts.policy_for(enum.source.module, "str_enum_values")
            style = (
                contract.spec.str_enum_values
                if contract and contract.spec.str_enum_values
                else POLICY_DEFAULTS.str_enum_values
            )
            if (style == "explicit" or enum.custom) and enum.automatic:
                yield _violation(
                    self,
                    enum,
                    f"StrEnum {enum.node.name} derives values with auto()"
                    if style == "explicit"
                    else f"StrEnum {enum.node.name} has custom values; spell its "
                    "auto() members explicitly",
                    style=style,
                    automatic=True,
                )
            elif style == "auto" and not enum.custom and enum.literal:
                yield _violation(
                    self,
                    enum,
                    f"StrEnum {enum.node.name} spells values that auto() derives",
                    style=style,
                    automatic=False,
                )


@dataclass(frozen=True, slots=True)
class _StrEnum:
    source: SourceFile
    node: ast.ClassDef
    automatic: list[str]
    literal: dict[str, str]

    @property
    def custom(self) -> bool:
        return any(value != name.lower() for name, value in self.literal.items())


def _str_enums(ctx: AnalysisContext) -> Iterator[_StrEnum]:
    for source in ctx.files.sources.values():
        tree = ctx.asts.parse(source.path)
        names = _EnumNames(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and names.is_str_enum(node):
                automatic, literal = _members(node, names)
                yield _StrEnum(source, node, automatic, literal)


def _violation(
    rule: Rule, enum: _StrEnum, message: str, *, style: str, automatic: bool
) -> Violation:
    """Report auto() members to spell out, or literal values auto() derives."""
    if automatic:
        members = enum.automatic
        hint = _spell(members)
    else:
        members = sorted(enum.literal)
        hint = "Use auto() for " + ", ".join(enum.literal) + "."
    return rule.violation(
        message,
        path=enum.source.path,
        line=enum.node.lineno,
        column=enum.node.col_offset + 1,
        source_module=enum.source.module,
        expected={
            "enum": enum.node.name,
            "members": members,
            "str_enum_values": style,
            "automatic_rewrite": False,
        },
        hint=hint,
    )


def _spell(members: list[str]) -> str:
    return (
        "Spell "
        + ", ".join(f'{member} = "{member.lower()}"' for member in members)
        + " explicitly."
    )


class _EnumNames:
    """Local names that refer to enum.StrEnum and enum.auto in one module."""

    def __init__(self, tree: ast.Module) -> None:
        self.modules: set[str] = set()
        self._str_enum: set[str] = set()
        self._auto: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                self.modules.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "enum"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "enum":
                for alias in node.names:
                    if alias.name == "StrEnum":
                        self._str_enum.add(alias.asname or alias.name)
                    elif alias.name == "auto":
                        self._auto.add(alias.asname or alias.name)

    def is_str_enum(self, node: ast.ClassDef) -> bool:
        return len(node.bases) == 1 and self._refers(
            node.bases[0], self._str_enum, "StrEnum"
        )

    def is_auto(self, value: ast.expr | None) -> bool:
        return (
            isinstance(value, ast.Call)
            and not value.args
            and not value.keywords
            and self._refers(value.func, self._auto, "auto")
        )

    def _refers(self, node: ast.expr, names: set[str], attribute: str) -> bool:
        if isinstance(node, ast.Name):
            return node.id in names
        return (
            isinstance(node, ast.Attribute)
            and node.attr == attribute
            and isinstance(node.value, ast.Name)
            and node.value.id in self.modules
        )


def _members(node: ast.ClassDef, names: _EnumNames) -> tuple[list[str], dict[str, str]]:
    automatic: list[str] = []
    literal: dict[str, str] = {}
    value: ast.expr | None
    for child in node.body:
        if isinstance(child, ast.Assign) and len(child.targets) == 1:
            target, value = child.targets[0], child.value
        elif isinstance(child, ast.AnnAssign):
            target, value = child.target, child.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id.startswith("_"):
            continue
        if names.is_auto(value):
            automatic.append(target.id)
        elif isinstance(value, ast.Constant) and isinstance(value.value, str):
            literal[target.id] = value.value
    return automatic, literal
