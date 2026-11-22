from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability, TextEdit
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.edits import SourceText, add_from_import, name_used, remove_aliases

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

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for enum in _str_enums(ctx):
            style = _value_style(ctx, enum.source.module)
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
    values: dict[str, ast.expr]
    tree: ast.Module
    names: _EnumNames
    text: SourceText

    @property
    def custom(self) -> bool:
        return any(value != name.lower() for name, value in self.literal.items())


def _str_enums(ctx: AnalysisContext) -> Iterator[_StrEnum]:
    for source in ctx.files.sources.values():
        tree = ctx.asts.parse(source.path)
        names = _EnumNames(tree)
        text: SourceText | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and names.is_str_enum(node):
                automatic, literal, values = _members(node, names)
                text = text or SourceText(ctx.files.read_text(source.path))
                yield _StrEnum(
                    source,
                    node,
                    automatic,
                    literal,
                    values=values,
                    tree=tree,
                    names=names,
                    text=text,
                )


def _violation(
    rule: Rule,
    enum: _StrEnum,
    message: str,
    *,
    style: str,
    automatic: bool,
) -> Violation:
    """Report auto() members to spell out, or literal values auto() derives."""
    if automatic:
        members = enum.automatic
        hint = _spell(members)
        fix = _explicit_fix(enum.text, enum.tree, enum)
    else:
        members = sorted(enum.literal)
        hint = "Use auto() for " + ", ".join(enum.literal) + "."
        fix = _auto_fix(enum.text, enum.tree, enum, names=enum.names)
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
            "automatic_rewrite": fix is not None,
        },
        hint=hint,
        fix=fix,
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

    def auto_spelling(self) -> str | None:
        """How this module names enum.auto, or None without an import for it."""
        if self._auto:
            return min(self._auto)
        return f"{min(self.modules)}.auto" if self.modules else None

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


def _members(
    node: ast.ClassDef, names: _EnumNames
) -> tuple[list[str], dict[str, str], dict[str, ast.expr]]:
    automatic: list[str] = []
    literal: dict[str, str] = {}
    values: dict[str, ast.expr] = {}
    value: ast.expr | None
    for child in node.body:
        if isinstance(child, ast.Assign) and len(child.targets) == 1:
            target, value = child.targets[0], child.value
        elif isinstance(child, ast.AnnAssign):
            target, value = child.target, child.value
        else:
            continue
        if (
            not isinstance(target, ast.Name)
            or target.id.startswith("_")
            or value is None
        ):
            continue
        if names.is_auto(value):
            automatic.append(target.id)
        elif isinstance(value, ast.Constant) and isinstance(value.value, str):
            literal[target.id] = value.value
        else:
            continue
        values[target.id] = value
    return automatic, literal, values


def _value_style(ctx: AnalysisContext, module: str) -> str:
    contract = ctx.contracts.policy_for(module, "str_enum_values")
    return (
        contract.spec.str_enum_values
        if contract and contract.spec.str_enum_values
        else POLICY_DEFAULTS.str_enum_values
    )


def _explicit_fix(source: SourceText, tree: ast.Module, enum: _StrEnum) -> Fix | None:
    """Spell each auto() member as its lowercased name.

    Safe: that is the value auto() produces for a StrEnum, unless the class
    overrides _generate_next_value_.
    """
    if _generates_values(enum.node):
        return None
    calls = [enum.values[name] for name in enum.automatic]
    edits = [
        source.replace(call, f'"{name.lower()}"')
        for name, call in zip(enum.automatic, calls, strict=True)
    ]
    edits.extend(_unused_auto(source, tree, calls))
    return Fix(
        Applicability.SAFE,
        f"Spell the values of {enum.node.name} explicitly",
        tuple(edits),
    )


def _auto_fix(
    source: SourceText, tree: ast.Module, enum: _StrEnum, *, names: _EnumNames
) -> Fix | None:
    """Replace explicit values that equal the lowercased name with auto().

    Safe: auto() derives the same value, unless the class overrides
    _generate_next_value_.
    """
    if _generates_values(enum.node):
        return None
    edits: list[TextEdit] = []
    spelling = names.auto_spelling()
    if spelling is None:
        if name_used(tree, "auto"):
            return None
        edits.append(add_from_import(source, tree, module="enum", name="auto"))
        spelling = "auto"
    edits.extend(
        source.replace(enum.values[name], f"{spelling}()") for name in enum.literal
    )
    return Fix(
        Applicability.SAFE,
        f"Derive the values of {enum.node.name} with auto()",
        tuple(edits),
    )


def _generates_values(node: ast.ClassDef) -> bool:
    return any(
        isinstance(child, ast.FunctionDef | ast.Assign)
        and "_generate_next_value_"
        in (
            [child.name]
            if isinstance(child, ast.FunctionDef)
            else [t.id for t in child.targets if isinstance(t, ast.Name)]
        )
        for child in node.body
    )


def _unused_auto(
    source: SourceText, tree: ast.Module, calls: list[ast.expr]
) -> Iterator[TextEdit]:
    """Remove a ``from enum import auto`` that only the replaced calls used."""
    for statement in tree.body:
        if not (isinstance(statement, ast.ImportFrom) and statement.module == "enum"):
            continue
        unused = [
            alias
            for alias in statement.names
            if alias.name == "auto"
            and not name_used(tree, alias.asname or alias.name, ignore=calls)
        ]
        if unused and (edit := remove_aliases(source, statement, unused)):
            yield edit
