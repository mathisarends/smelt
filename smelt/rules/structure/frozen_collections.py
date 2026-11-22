from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.bindings import annotation_name, qualified_name, unshadowed_bindings
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_ALTERNATIVES = {
    "builtins.dict": "a defensive copy behind MappingProxyType, exposed as Mapping",
    "builtins.list": "tuple",
    "builtins.set": "frozenset",
}


class MutableFieldInFrozenDataclass(Rule):
    file_local = True
    code = "MOD011"
    name = "modeling/mutable-field-in-frozen-dataclass"
    category = Category.MODELING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A frozen dataclass directly declares a mutable collection field.",
        rationale=(
            "frozen=True prevents rebinding fields, but list, dict and set contents "
            "can still change, including through a reference passed to the "
            "constructor. This opt-in ownership review reports each directly "
            "annotated built-in dict, list or set instance field on a dataclass "
            "explicitly decorated with frozen=True. It resolves module/import "
            "aliases and quoted annotations, conservatively skipping names bound "
            "again anywhere in the file, local imports and wildcard imports. "
            "ClassVar, InitVar, inherited fields, wrapped/union annotations, "
            "user-defined types and type aliases are outside its scope. Non-frozen "
            "classes and tests are ignored. A Mapping annotation is a read-only "
            "interface, not proof of runtime or deep immutability. Intentional "
            "mutable reference holders can keep a reasoned field suppression."
        ),
        bad=(
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class Arguments:\n"
            "    names: dict[str, str]"
        ),
        good=(
            "from collections.abc import Mapping\n"
            "from dataclasses import dataclass\n"
            "from types import MappingProxyType\n\n"
            "@dataclass(frozen=True)\n"
            "class Arguments:\n"
            "    names: Mapping[str, str]\n\n"
            "    def __post_init__(self) -> None:\n"
            '        object.__setattr__(self, "names", MappingProxyType(dict(self.names)))'
        ),
        fix=(
            "Review ownership. Use tuple or frozenset for immutable sequences/sets. "
            "For a mapping, copy before wrapping it in MappingProxyType: wrapping "
            "the caller's original dict alone still reflects alias mutations. "
            "Changing only the annotation to Mapping freezes nothing. Copying or "
            "freezing may change behavior, so no automatic rewrite is performed. "
            "Enable rules: {modeling/mutable-field-in-frozen-dataclass: warning}, or select MOD011."
        ),
        canonical=(
            "Use tuple, frozenset or a read-only mapping for fields of frozen"
            " dataclasses"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            bindings = unshadowed_bindings(tree, builtin_names=("dict", "list", "set"))
            for owner in tree_nodes(tree):
                if not isinstance(owner, ast.ClassDef) or not _is_frozen(
                    owner, bindings
                ):
                    continue
                for field in owner.body:
                    if not isinstance(field, ast.AnnAssign) or not isinstance(
                        field.target, ast.Name
                    ):
                        continue
                    kind = annotation_name(field.annotation, bindings)
                    if kind is None or kind not in _ALTERNATIVES:
                        continue
                    yield self.violation(
                        f"Frozen dataclass {owner.name}.{field.target.id} contains "
                        f"mutable {kind.removeprefix('builtins.')} contents",
                        path=source.path,
                        line=field.lineno,
                        column=field.col_offset + 1,
                        source_module=source.module,
                        expected={
                            "class": owner.name,
                            "field": field.target.id,
                            "mutable_type": kind,
                            "automatic_rewrite": False,
                        },
                        hint=f"Review ownership; consider {_ALTERNATIVES[kind]}.",
                    )


def _is_frozen(owner: ast.ClassDef, bindings: Mapping[str, str]) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and qualified_name(decorator.func, bindings) == "dataclasses.dataclass"
        and not any(keyword.arg is None for keyword in decorator.keywords)
        and any(
            keyword.arg == "frozen"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        )
        for decorator in owner.decorator_list
    )
