from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.bindings import (
    annotation_name,
    binding_counts,
    qualified_name,
    unshadowed_bindings,
)
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import child_nodes, tree_nodes
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

type _Method = ast.FunctionDef | ast.AsyncFunctionDef

_SEQUENCES = frozenset(
    {
        "builtins.list",
        "builtins.tuple",
        "typing.List",
        "typing.Tuple",
        "typing.Sequence",
        "typing.MutableSequence",
        "collections.abc.Sequence",
        "collections.abc.MutableSequence",
    }
)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_MIN_FIELDS = 2


class ParallelRecordFields(Rule):
    code = "MOD012"
    name = "modeling/parallel-record-fields"
    category = Category.MODELING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Stored sequence fields of one instance are paired by position.",
        rationale=(
            "Parallel stored sequences leave their record relationship implicit: "
            "every constructor and transformation must preserve matching positions. "
            "zip(strict=True) checks lengths but cannot check the right pairing. "
            "This opt-in design review groups zip calls over at least two distinct "
            "sequence fields on the same instance, once per class and field group, "
            "with declaration and zip locations. It checks directly declared class "
            "annotations of list, tuple, Sequence and MutableSequence, including "
            "typing spellings, quoted annotations and module/import aliases. "
            "Preceding unconditional local aliases assigned once from a receiver "
            "field are supported. "
            "Only undecorated instance methods are inspected. Rebound names, starred "
            "arguments, inherited/untyped fields, ClassVar, nested functions and "
            "unknown types are skipped. Local sequences and different receivers "
            "remain valid. Tests are ignored. The rule cannot establish that the "
            "fields form a domain record: intentional column-oriented or numerical "
            "storage is valid and can use a reasoned suppression at the reported zip site."
        ),
        bad=(
            "class Layout:\n"
            "    models: tuple[type, ...]\n"
            "    fields: tuple[tuple[str, ...], ...]\n\n"
            "    def split(self):\n"
            "        for model, names in zip(self.models, self.fields, strict=True):\n"
            "            yield model, names"
        ),
        good=(
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class Binding:\n"
            "    model: type\n"
            "    names: tuple[str, ...]\n\n"
            "class Layout:\n"
            "    bindings: tuple[Binding, ...]\n\n"
            "    def split(self):\n"
            "        for binding in self.bindings:\n"
            "            yield binding.model, binding.names\n\n"
            "class Columns:\n"
            "    x: list[float]\n"
            "    y: list[float]\n\n"
            "    def points(self):\n"
            "        # smelt: ignore[MOD012] -- deliberate numerical column storage\n"
            "        return zip(self.x, self.y, strict=True)"
        ),
        fix=(
            "Review whether these fields model one logical record. If so, store "
            "one sequence of named records and update construction and consumers "
            "together. Preserve length validation and iteration order. Deliberate "
            "column storage may retain a reasoned suppression. No automatic rewrite "
            "or correctness claim is made. Enable rules: {modeling/parallel-record-fields: hint}, "
            "or select MOD012."
        ),
        canonical=(
            "Store one sequence of named records instead of parallel sequence fields"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            bindings = unshadowed_bindings(tree, builtin_names=("zip", "list", "tuple"))
            for owner in tree_nodes(tree):
                if not isinstance(owner, ast.ClassDef):
                    continue
                fields = _fields(owner, bindings)
                for group in _groups(owner, fields, bindings=bindings):
                    first = group.sites[0]
                    names = [
                        field.target.id
                        for field in group.fields
                        if isinstance(field.target, ast.Name)
                    ]
                    yield self.violation(
                        f"Stored collections {', '.join(names)} on {owner.name} are paired "
                        "by position; consider one collection of named records",
                        path=source.path,
                        line=first.lineno,
                        column=first.col_offset + 1,
                        source_module=source.module,
                        expected={
                            "class": owner.name,
                            "fields": names,
                            "pairing_edges": [f"{owner.name}: {', '.join(names)}"],
                            "declarations": [
                                {
                                    "field": name,
                                    "line": field.lineno,
                                    "column": field.col_offset + 1,
                                }
                                for name, field in zip(names, group.fields, strict=True)
                            ],
                            "zip_sites": [
                                {"line": site.lineno, "column": site.col_offset + 1}
                                for site in group.sites
                            ],
                            "automatic_rewrite": False,
                        },
                        hint="Make the record relationship explicit, or document intentional column storage.",
                    )


@dataclass(frozen=True, slots=True)
class _FieldGroup:
    fields: tuple[ast.AnnAssign, ...]
    sites: tuple[ast.Call, ...]


def _fields(
    owner: ast.ClassDef, bindings: Mapping[str, str]
) -> dict[str, ast.AnnAssign]:
    declarations = [node for node in owner.body if not isinstance(node, _SCOPES)]
    counts = binding_counts(ast.Module(body=declarations, type_ignores=[]))
    hidden = {
        node.name
        for node in owner.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    return {
        field.target.id: field
        for field in owner.body
        if isinstance(field, ast.AnnAssign)
        and isinstance(field.target, ast.Name)
        and counts[field.target.id] == 1
        and field.target.id not in hidden
        and annotation_name(field.annotation, bindings) in _SEQUENCES
    }


def _groups(
    owner: ast.ClassDef,
    fields: Mapping[str, ast.AnnAssign],
    *,
    bindings: Mapping[str, str],
) -> Iterator[_FieldGroup]:
    sites: dict[tuple[str, ...], list[ast.Call]] = defaultdict(list)
    for method in owner.body:
        if (
            not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
            or method.decorator_list
        ):
            continue
        args = [*method.args.posonlyargs, *method.args.args]
        if not args:
            continue
        receiver = args[0].arg
        nodes = list(_method_nodes(method))
        if any(
            isinstance(node, ast.Name)
            and node.id == receiver
            and isinstance(node.ctx, ast.Store | ast.Del)
            for node in nodes
        ):
            continue
        for node in nodes:
            if (
                not isinstance(node, ast.Call)
                or qualified_name(node.func, bindings) != "builtins.zip"
            ):
                continue
            aliases = _aliases(method, receiver, before=node.lineno)
            names = [_field_name(arg, receiver, aliases) for arg in node.args]
            if any(name not in fields for name in names):
                continue
            group = tuple(sorted({name for name in names if name is not None}))
            if len(group) >= _MIN_FIELDS:
                sites[group].append(node)
    for group_names, calls in sites.items():
        yield _FieldGroup(
            tuple(fields[name] for name in group_names),
            tuple(sorted(calls, key=lambda site: (site.lineno, site.col_offset))),
        )


def _aliases(method: _Method, receiver: str, *, before: int) -> dict[str, str]:
    counts = binding_counts(ast.Module(body=[method], type_ignores=[]))
    aliases: dict[str, str] = {}
    for node in method.body:
        if node.lineno >= before:
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if (
            isinstance(target, ast.Name)
            and counts[target.id] == 1
            and (name := _field_name(value, receiver, {}))
        ):
            aliases[target.id] = name
    return aliases


def _field_name(
    node: ast.expr, receiver: str, aliases: Mapping[str, str]
) -> str | None:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == receiver
    ):
        return node.attr
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    return None


def _method_nodes(method: _Method) -> Iterator[ast.AST]:
    pending: list[ast.AST] = list(method.body)
    for node in pending:
        if isinstance(node, _SCOPES):
            continue
        yield node
        pending.extend(child_nodes(node))
