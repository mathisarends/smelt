from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class MixedStrEnumValues(Rule):
    file_local = True
    code = "SMT341"
    name = "mixed-str-enum-values"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A StrEnum mixes auto() members with explicit string values.",
        rationale=(
            "A StrEnum should declare its values in one canonical form: either every "
            "member uses auto(), which derives the lowercased member name, or every "
            "member spells its string explicitly. Mixing both hides which values are "
            "wire contracts and which are derived, and makes readers check each line. "
            "This opt-in review reports classes in production code that inherit "
            "directly from enum.StrEnum (imported as StrEnum, an alias or enum.StrEnum) "
            "and declare at least one auto() member and at least one string literal "
            "member. Member aliases, private names and other assignments are ignored. "
            "Tests are not checked."
        ),
        bad=(
            "class SignalingMessageType(StrEnum):\n"
            "    DESCRIPTION = auto()\n"
            '    CANDIDATE = "candidate"\n'
            "    READY = auto()"
        ),
        good=(
            "class SignalingMessageType(StrEnum):\n"
            "    DESCRIPTION = auto()\n"
            "    CANDIDATE = auto()\n"
            "    READY = auto()"
        ),
        fix=(
            "When every explicit string equals the lowercased member name, replace "
            "those strings with auto(). Otherwise the explicit values are deliberate, "
            "so spell every auto() member as its lowercased name instead. The finding "
            "names the suggested style. No automatic rewrite is performed. Enable "
            "rules: {mixed-str-enum-values: hint}, or select SMT341. Package overrides "
            "and reasoned inline suppressions are supported."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            names = _EnumNames(tree)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.ClassDef) and names.is_str_enum(node)):
                    continue
                automatic, literal = _members(node, names)
                if not automatic or not literal:
                    continue
                derivable = all(
                    value == member.lower() for member, value in literal.items()
                )
                style = "auto" if derivable else "literal"
                hint = (
                    "Every explicit value equals the lowercased member name; use "
                    "auto() for " + ", ".join(literal) + "."
                    if derivable
                    else "Explicit values differ from the member names; spell "
                    + ", ".join(
                        f'{member} = "{member.lower()}"' for member in automatic
                    )
                    + " explicitly."
                )
                yield self.violation(
                    f"StrEnum {node.name} mixes auto() and explicit string values",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "enum": node.name,
                        "auto_members": automatic,
                        "literal_members": sorted(literal),
                        "suggested_style": style,
                        "automatic_rewrite": False,
                    },
                    hint=hint,
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
