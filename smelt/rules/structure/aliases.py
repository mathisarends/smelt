from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_TYPING = frozenset({"typing", "typing_extensions"})


class RedundantNameAlias(Rule):
    file_local = True
    code = "SMT344"
    name = "redundant-name-alias"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module-level name only repeats another class or function name.",
        rationale=(
            "Two names for one class or function make readers and agents ask "
            "whether they differ, and grep finds only half of the uses. Such "
            "aliases are typically compatibility names left behind by a rename or "
            "placeholders for a union that never gained a second member. This "
            "opt-in review reports unconditional module-level X = Y, "
            "X: TypeAlias = Y and type X = Y statements whose right side is a bare "
            "name or dotted attribute that refers to a class or function defined "
            "in the same module, or to an imported CapWords name. Type "
            "expressions such as unions, subscripts and Annotated, values such as "
            "constants, conditional assignments (version or import fallbacks), "
            "class-level names, typing special forms such as Any and tests are not "
            "reported."
        ),
        bad=(
            "class PasswordData(BaseModel):\n"
            "    username: str\n\n"
            "AutofillData = PasswordData"
        ),
        good=(
            "class PasswordData(BaseModel):\n"
            "    username: str\n\n"
            "type AutofillData = PasswordData | CardData"
        ),
        fix=(
            "Use the target name everywhere and remove the alias. Introduce a "
            "shared name once it describes a real type expression, such as a union "
            "with a second member. Keep a deliberate public compatibility name with "
            "a reasoned suppression. No automatic rewrite is performed. Enable "
            "rules: {redundant-name-alias: hint}, or select SMT344."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            targets = _Targets(tree)
            for node in tree.body:
                alias = _alias(node)
                if alias is None:
                    continue
                name, value, form = alias
                target = targets.describe(value)
                if target is None or name == _dotted(value):
                    continue
                kind, spelled = target
                yield self.violation(
                    f"{name} is a second name for {kind} {spelled}",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "alias": name,
                        "target": spelled,
                        "target_kind": kind,
                        "form": form,
                        "automatic_rewrite": False,
                    },
                    hint=f"Use {spelled} directly and remove {name}.",
                )


def _alias(node: ast.stmt) -> tuple[str, ast.expr, str] | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value, "assignment"
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
        and _dotted(node.annotation) in {"TypeAlias", "typing.TypeAlias"}
    ):
        return node.target.id, node.value, "TypeAlias"
    if isinstance(node, ast.TypeAlias) and not node.type_params:
        return node.name.id, node.value, "type"
    return None


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (owner := _dotted(node.value)):
        return f"{owner}.{node.attr}"
    return None


def _cap_words(name: str) -> bool:
    return name[:1].isupper() and not name.isupper()


class _Targets:
    """Classes and functions one module defines, and the names it imports."""

    def __init__(self, tree: ast.Module) -> None:
        self.definitions: dict[str, str] = {}
        self.imported: set[str] = set()
        self.modules: set[str] = set()
        rebound: set[str] = set()
        for node in tree.body:
            match node:
                case ast.ClassDef() | ast.FunctionDef() | ast.AsyncFunctionDef():
                    if node.name in self.definitions:
                        rebound.add(node.name)
                    self.definitions[node.name] = (
                        "class" if isinstance(node, ast.ClassDef) else "function"
                    )
                case ast.ImportFrom() if node.module not in _TYPING:
                    self.imported.update(
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name != "*"
                    )
                case ast.Import():
                    self.modules.update(
                        alias.asname or alias.name.partition(".")[0]
                        for alias in node.names
                        if alias.name not in _TYPING
                    )
        for name in rebound:
            self.definitions.pop(name, None)

    def describe(self, value: ast.expr) -> tuple[str, str] | None:
        spelled = _dotted(value)
        if spelled is None:
            return None
        if isinstance(value, ast.Name):
            if kind := self.definitions.get(spelled):
                return kind, spelled
            if spelled in self.imported and _cap_words(spelled):
                return "class", spelled
            return None
        head, _, _ = spelled.partition(".")
        last = spelled.rpartition(".")[2]
        if head in self.modules and _cap_words(last):
            return "class", spelled
        return None
