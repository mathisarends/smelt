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

_AUTO = "auto"
_LITERAL = "literal"


class MixedStrEnumValues(Rule):
    code = "SMT341"
    name = "mixed-str-enum-values"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="StrEnum values are declared with auto() in one place and as "
        "explicit strings in another.",
        rationale=(
            "StrEnum values should follow one canonical form across a project: "
            "either auto(), which derives the lowercased member name, or explicit "
            "strings. Mixing both, inside one class or between classes, hides which "
            "values are wire contracts and which are derived. This opt-in review "
            "considers production classes inheriting directly from enum.StrEnum "
            "(imported as StrEnum, an alias or enum.StrEnum). A class whose explicit "
            "strings all equal the lowercased member names could use either form; "
            "the form used by most such classes is the project style, auto() on a "
            "tie. A class is reported when it mixes auto() and explicit strings, or "
            "when it uses only the other form than the project style. Classes with "
            "custom values such as 'mouseMoved' must spell their strings and never "
            "decide or violate the project style, but auto() members inside them "
            "are reported. Member aliases, private names and tests are ignored."
        ),
        bad=(
            "class SignalingMessageType(StrEnum):\n"
            "    DESCRIPTION = auto()\n"
            "    READY = auto()\n\n"
            "class ConnectionState(StrEnum):\n"
            '    OPEN = "open"\n'
            '    CLOSED = "closed"'
        ),
        good=(
            "class SignalingMessageType(StrEnum):\n"
            "    DESCRIPTION = auto()\n"
            "    READY = auto()\n\n"
            "class ConnectionState(StrEnum):\n"
            "    OPEN = auto()\n"
            "    CLOSED = auto()"
        ),
        fix=(
            "Rewrite the reported class in the suggested style: replace explicit "
            "strings equal to the lowercased member name with auto(), or spell every "
            "auto() member as its lowercased name. Classes with custom values keep "
            "explicit strings. The finding names the project style and how many "
            "classes use each form. No automatic rewrite is performed. Enable "
            "rules: {mixed-str-enum-values: hint}, or select SMT341. Package "
            "overrides and reasoned inline suppressions are supported."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        enums = [
            enum
            for source in ctx.files.sources.values()
            for enum in _str_enums(source, ctx.asts.parse(source.path))
        ]
        derivable = [enum for enum in enums if enum.derivable]
        counts = {
            style: sum(enum.style == style for enum in derivable)
            for style in (_AUTO, _LITERAL)
        }
        project = _LITERAL if counts[_LITERAL] > counts[_AUTO] else _AUTO
        for enum in enums:
            suggested = project if enum.derivable else _LITERAL
            if enum.style == suggested:
                continue
            yield self.violation(
                _message(enum, project),
                path=enum.source.path,
                line=enum.node.lineno,
                column=enum.node.col_offset + 1,
                source_module=enum.source.module,
                expected={
                    "enum": enum.node.name,
                    "auto_members": enum.automatic,
                    "literal_members": sorted(enum.literal),
                    "suggested_style": suggested,
                    "project_style": project,
                    "auto_enums": counts[_AUTO],
                    "literal_enums": counts[_LITERAL],
                    "automatic_rewrite": False,
                },
                hint=_hint(enum, suggested),
            )


@dataclass(frozen=True)
class _StrEnum:
    source: SourceFile
    node: ast.ClassDef
    automatic: list[str]
    literal: dict[str, str]

    @property
    def derivable(self) -> bool:
        return all(value == name.lower() for name, value in self.literal.items())

    @property
    def style(self) -> str | None:
        if self.automatic and self.literal:
            return None
        if self.automatic:
            return _AUTO
        return _LITERAL if self.literal else None


def _str_enums(source: SourceFile, tree: ast.Module) -> Iterator[_StrEnum]:
    names = _EnumNames(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and names.is_str_enum(node):
            automatic, literal = _members(node, names)
            if automatic or literal:
                yield _StrEnum(source, node, automatic, literal)


def _message(enum: _StrEnum, project: str) -> str:
    if enum.style is None:
        return f"StrEnum {enum.node.name} mixes auto() and explicit string values"
    if enum.style == _LITERAL:
        return (
            f"StrEnum {enum.node.name} spells values that auto() derives; "
            "the project declares StrEnum values with auto()"
        )
    return (
        f"StrEnum {enum.node.name} uses auto(); the project declares "
        f"StrEnum values as explicit strings"
        if project == _LITERAL
        else f"StrEnum {enum.node.name} uses auto() next to custom string values"
    )


def _hint(enum: _StrEnum, suggested: str) -> str:
    if suggested == _AUTO:
        return "Use auto() for " + ", ".join(enum.literal) + "."
    return (
        "Spell "
        + ", ".join(f'{member} = "{member.lower()}"' for member in enum.automatic)
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
