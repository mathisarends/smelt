from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class ExcessPrivateDefinitions(Rule):
    file_local = True
    code = "SMT367"
    name = "visibility/excess-private-definitions"
    category = Category.VISIBILITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module contains more private definitions than the configured limit.",
        rationale=(
            "A growing collection of private models and helpers can make a module's "
            "responsibility harder to understand. This opt-in review counts private "
            "classes, sync/async functions and PEP 695 type aliases directly in a "
            "production module body. A leading underscore marks privacy; dunders "
            "are excluded. Repeated declarations of the same name and kind, including "
            "overloads, count once at their first declaration. Private uppercase "
            "assignments with values are listed separately as constants and never "
            "contribute to the limit. Imports, ordinary assignment-style type aliases, "
            "methods, nested/local definitions and definitions inside control-flow "
            "statements are excluded. Package initializers are checked too. A few "
            "models beside their consumer remain valid at the default limit of 12. "
            "Even a larger cohesive model tree or parser may be intentional: this "
            "metric does not infer cohesion, separable groups or safe extraction."
        ),
        bad=(
            "# With structure.max_private_definitions: 2\n"
            "class _Cookie: pass\n"
            "class _StorageItem: pass\n"
            "def _merge(existing, incoming): ...\n"
            "class AuthenticationState: ..."
        ),
        good=(
            "# A small local model stays beside its consumer.\n"
            "class _Cookie: pass\n"
            "class AuthenticationState: ..."
        ),
        fix=(
            "Review the listed definitions for a cohesive group that deserves its "
            "own module, carrying its constants and validators with it. Choose a "
            "name describing that responsibility; models.py is only one possibility. "
            "Preserve import direction, initialization, framework registration and "
            "module visibility when moving code. Do not make helpers public or "
            "extract arbitrary fragments merely to lower the count. Keep deliberate "
            "cohesive implementations with a reasoned suppression or package override. "
            "No automatic rewrite is performed. Enable rules: "
            "{visibility/excess-private-definitions: hint} or select SMT367. "
            "structure.max_private_definitions defaults to 12; closest package "
            "overrides, including zero, are supported. Setting a threshold alone "
            "does not enable the rule."
        ),
        config=("rules", "structure.max_private_definitions", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            limit = ctx.config.structure.max_private_definitions
            contract = ctx.contracts.policy_for(
                source.module, "max_private_definitions"
            )
            if (
                contract is not None
                and contract.spec.max_private_definitions is not None
            ):
                limit = contract.spec.max_private_definitions
            declarations = _declarations(ctx.asts.parse(source.path))
            definitions = [item for item in declarations if item.kind != "constant"]
            if len(definitions) <= limit:
                continue
            constants = [item for item in declarations if item.kind == "constant"]
            counts = Counter(item.kind for item in definitions)
            first = definitions[0].node
            yield self.violation(
                f"Module {source.module} contains {len(definitions)} private definitions "
                f"(limit {limit}): {counts['class']} classes, {counts['function']} functions, "
                f"{counts['type_alias']} type aliases; review its responsibilities",
                path=source.path,
                line=first.lineno,
                column=first.col_offset + 1,
                source_module=source.module,
                expected={
                    "max_private_definitions": limit,
                    "count": len(definitions),
                    "counts": {
                        "classes": counts["class"],
                        "functions": counts["function"],
                        "type_aliases": counts["type_alias"],
                    },
                    "definitions": _locations(definitions),
                    "constant_count": len(constants),
                    "constants": _locations(constants),
                    "definition_edges": [
                        f"{item.kind}:{item.name}" for item in definitions
                    ],
                    "automatic_rewrite": False,
                },
                hint="Review cohesive models or processing steps for a separate module, including their constants and validators; preserve import direction and module visibility. A large cohesive implementation may be intentional.",
            )


@dataclass(frozen=True, slots=True)
class _Definition:
    name: str
    kind: Literal["class", "function", "type_alias", "constant"]
    node: ast.stmt | ast.Name


def _declarations(tree: ast.Module) -> list[_Definition]:
    declarations: dict[tuple[str, str], _Definition] = {}
    for statement in tree.body:
        for item in _statement_definitions(statement):
            if item.name.startswith("_") and not (
                item.name.startswith("__") and item.name.endswith("__")
            ):
                declarations.setdefault((item.kind, item.name), item)
    return list(declarations.values())


def _statement_definitions(statement: ast.stmt) -> Iterator[_Definition]:
    match statement:
        case ast.ClassDef(name=name):
            yield _Definition(name, "class", statement)
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
            yield _Definition(name, "function", statement)
        case ast.TypeAlias(name=ast.Name(id=name)):
            yield _Definition(name, "type_alias", statement)
        case ast.Assign(targets=targets) | ast.AnnAssign(target=targets) if (
            statement.value is not None
        ):
            for target in targets if isinstance(targets, list) else [targets]:
                if isinstance(target, ast.Name) and target.id.isupper():
                    yield _Definition(target.id, "constant", target)


def _locations(definitions: list[_Definition]) -> list[dict[str, str | int]]:
    return [
        {
            "name": item.name,
            "kind": item.kind,
            "line": item.node.lineno,
            "column": item.node.col_offset + 1,
        }
        for item in definitions
    ]
