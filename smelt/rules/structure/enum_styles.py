from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile

_SCOPE = (
    "It considers production classes inheriting directly from enum.StrEnum "
    "(imported as StrEnum, an alias or enum.StrEnum). Member aliases, private "
    "names and tests are ignored."
)


class StrEnumAutoValues(Rule):
    file_local = True
    code = "SMT341"
    name = "str-enum-auto-values"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A StrEnum spells a value that auto() derives.",
        rationale=(
            "This opt-in style rule makes auto() the project convention for StrEnum "
            "values: auto() derives the lowercased member name, so an explicit "
            "string repeating it is noise and hides which values are deliberate "
            "wire contracts. A class is reported when an explicit string equals the "
            "lowercased member name. A class with custom values such as "
            "'mouseMoved' keeps explicit strings for every member; there, auto() "
            "members are reported instead. Do not enable it together with SMT342 "
            "str-enum-explicit-values, which states the opposite convention. " + _SCOPE
        ),
        bad=(
            'class ConnectionState(StrEnum):\n    OPEN = "open"\n    CLOSED = "closed"'
        ),
        good=(
            "class ConnectionState(StrEnum):\n    OPEN = auto()\n    CLOSED = auto()"
        ),
        fix=(
            "Replace each reported explicit string with auto(). In a class with "
            "custom values, spell the reported auto() members as their lowercased "
            "names instead. No automatic rewrite is performed. Enable "
            "rules: {str-enum-auto-values: hint}, or select SMT341. Package "
            "overrides and reasoned inline suppressions are supported."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for enum in _str_enums(ctx):
            if enum.custom:
                if enum.automatic:
                    yield _violation(
                        self,
                        enum,
                        f"StrEnum {enum.node.name} has custom values; spell its "
                        "auto() members explicitly",
                        enum.automatic,
                        _spell(enum.automatic),
                    )
            elif enum.literal:
                yield _violation(
                    self,
                    enum,
                    f"StrEnum {enum.node.name} spells values that auto() derives",
                    sorted(enum.literal),
                    "Use auto() for " + ", ".join(enum.literal) + ".",
                )


class StrEnumExplicitValues(Rule):
    file_local = True
    code = "SMT342"
    name = "str-enum-explicit-values"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A StrEnum derives a value with auto().",
        rationale=(
            "This opt-in style rule makes explicit strings the project convention "
            "for StrEnum values: every value is visible where it is declared and "
            "renaming a member cannot silently change a serialized value. A class "
            "is reported when a member uses auto(). Do not enable it together with "
            "SMT341 str-enum-auto-values, which states the opposite convention. "
            + _SCOPE
        ),
        bad=(
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
            "Spell each reported auto() member as its lowercased name, the value "
            "auto() produces for a StrEnum. No automatic rewrite is performed. "
            "Enable rules: {str-enum-explicit-values: hint}, or select SMT342. "
            "Package overrides and reasoned inline suppressions are supported."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for enum in _str_enums(ctx):
            if enum.automatic:
                yield _violation(
                    self,
                    enum,
                    f"StrEnum {enum.node.name} derives values with auto()",
                    enum.automatic,
                    _spell(enum.automatic),
                )


@dataclass(frozen=True)
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
    rule: Rule, enum: _StrEnum, message: str, members: list[str], hint: str
) -> Violation:
    return rule.violation(
        message,
        path=enum.source.path,
        line=enum.node.lineno,
        column=enum.node.col_offset + 1,
        source_module=enum.source.module,
        expected={
            "enum": enum.node.name,
            "members": members,
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
        self.str_enum: set[str] = set()
        self.auto: set[str] = set()
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
                        self.str_enum.add(alias.asname or alias.name)
                    elif alias.name == "auto":
                        self.auto.add(alias.asname or alias.name)

    def is_str_enum(self, node: ast.ClassDef) -> bool:
        return len(node.bases) == 1 and self._refers(
            node.bases[0], self.str_enum, "StrEnum"
        )

    def is_auto(self, value: ast.expr | None) -> bool:
        return (
            isinstance(value, ast.Call)
            and not value.args
            and not value.keywords
            and self._refers(value.func, self.auto, "auto")
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
