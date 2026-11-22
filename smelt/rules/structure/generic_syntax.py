from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from smelt.analysis.bindings import binding_counts
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.analysis.typing_bindings import typing_bindings, typing_name
from smelt.diagnostics.fixes import (
    Applicability,
    Fix,
    FixAvailability,
    TextEdit,
    TextSpan,
)
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.edits import SourceText, name_used, remove_aliases, top_level_import

if TYPE_CHECKING:
    from collections import Counter
    from collections.abc import Iterator

_INLINE_TYPE_PARAMETERS = (3, 12)


class PreferInlineTypeParameters(Rule):
    file_local = True
    code = "TYP008"
    name = "typing/prefer-inline-type-parameters"
    category = Category.TYPING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A generic class uses an explicit Generic[...] base on a Python 3.12+ target.",
        rationale=(
            "PEP 695 puts type parameters on the class declaration and scopes "
            "them to the class. This opt-in rule reports parameterized Generic "
            "bases resolved through stable, direct module imports from typing or "
            "typing_extensions, including aliases. It uses project.python_version "
            "or the owning project's requires-python. A minimum below 3.12 or "
            "an unknown minimum leaves legacy syntax valid. Generic subclasses "
            "without an explicit Generic base, functions and type aliases are "
            "outside this check. Ruff UP046 covers similar syntax. Use this "
            "rule to apply Smelt's version and package policy."
        ),
        bad=(
            "from typing import Generic, TypeVar\n\n"
            'T = TypeVar("T")\n\n'
            "class Box(Generic[T]):\n"
            "    value: T"
        ),
        good="class Box[T]:\n    value: T",
        fix=(
            "Declare the parameters on the class, remove the Generic base and "
            "keep any other bases. Preserve parameter order, bounds and "
            "constraints, and check whether the target supports any parameter "
            "defaults. Review inferred variance, shared type variable uses and "
            "runtime introspection before migrating. A class that needs to retain "
            "explicit variance can use a reasoned Smelt suppression. "
            "check --fix --unsafe-fixes migrates classes whose parameters are "
            "module-level TypeVar, ParamSpec or TypeVarTuple declarations without "
            "variance or defaults, and removes declarations and imports nothing "
            "else uses; inferred variance can differ from the declared one. Enable "
            "rules: {typing/prefer-inline-type-parameters: hint}, or select TYP008."
        ),
        canonical=(
            "Declare type parameters inline (class Box[T]:) instead of a "
            "Generic[T] base"
        ),
        config=("rules", "style.overrides", "project.python_version"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            target = ctx.python_targets.for_path(source.absolute)
            if target.minimum is None or target.minimum < _INLINE_TYPE_PARAMETERS:
                continue
            tree = ctx.asts.parse(source.path)
            bindings = typing_bindings(tree)
            text: SourceText | None = None
            for node in tree_nodes(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                generic = next(
                    (
                        base
                        for base in node.bases
                        if isinstance(base, ast.Subscript)
                        and typing_name(base.value, bindings) == "Generic"
                    ),
                    None,
                )
                if generic is None:
                    continue
                text = text or SourceText(ctx.files.read_text(source.path))
                fix = _fix(text, tree, node=node, generic=generic)
                yield self.violation(
                    f"declare type parameters inline on class {node.name}",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": node.name,
                        "python": target.to_json(),
                        "automatic_rewrite": fix is not None,
                    },
                    hint=(
                        "Use PEP 695 class type parameters in place of the Generic "
                        "base after checking inferred variance and shared type variable uses."
                    ),
                    fix=fix,
                )


# The declaration calls the fix can translate, by their typing name.
_DECLARATIONS = frozenset({"TypeVar", "ParamSpec", "TypeVarTuple"})


def _fix(
    source: SourceText, tree: ast.Module, *, node: ast.ClassDef, generic: ast.Subscript
) -> Fix | None:
    """Move the Generic parameters onto the class declaration.

    Unsafe: PEP 695 parameters infer their variance, and the class gets its own
    parameter objects instead of the shared module-level ones.
    """
    if node.type_params:
        return None
    bindings = typing_bindings(tree)
    counts = binding_counts(tree)
    elements = (
        generic.slice.elts if isinstance(generic.slice, ast.Tuple) else [generic.slice]
    )
    parameters: list[str] = []
    declarations: list[tuple[str, ast.Assign]] = []
    for element in elements:
        declared = _declaration(source, tree, element, context=(bindings, counts))
        if declared is None:
            return None
        parameter, name, declaration = declared
        parameters.append(parameter)
        declarations.append((name, declaration))
    header = _header(source, node, generic, parameters=parameters)
    if header is None:
        return None
    edits = [header]
    removed: list[ast.AST] = [generic]
    for name, declaration in declarations:
        if name_used(tree, name, ignore=[node, declaration]):
            continue
        if not source.stands_alone(declaration):
            return None
        edits.append(source.delete_lines(declaration))
        removed.append(declaration)
    edits.extend(_unused_imports(source, tree, removed=removed, bindings=bindings))
    return Fix(
        Applicability.UNSAFE,
        f"Declare the type parameters of {node.name} inline",
        tuple(edits),
    )


def _declaration(
    source: SourceText,
    tree: ast.Module,
    element: ast.expr,
    *,
    context: tuple[dict[str, str], Counter[str]],
) -> tuple[str, str, ast.Assign] | None:
    """The inline parameter, its name and its module-level declaration.

    `context` holds the typing bindings and the module's binding counts.
    """
    bindings, counts = context
    starred = isinstance(element, ast.Starred)
    if isinstance(element, ast.Starred):
        element = element.value
    if not isinstance(element, ast.Name) or counts[element.id] != 1:
        return None
    name = element.id
    for statement in tree.body:
        match statement:
            case ast.Assign(
                targets=[ast.Name(id=target)], value=ast.Call() as call
            ) if target == name:
                pass
            case _:
                continue
        kind = typing_name(call.func, bindings)
        if (
            kind not in _DECLARATIONS
            or (kind == "TypeVarTuple") != starred
            or not call.args
            or not isinstance(call.args[0], ast.Constant)
            or call.args[0].value != name
            or any(isinstance(argument, ast.Starred) for argument in call.args)
            or (kind != "TypeVar" and len(call.args) > 1)
            or any(
                keyword.arg != "bound" or kind != "TypeVar" for keyword in call.keywords
            )
        ):
            return None
        return _parameter(source, kind, name=name, call=call), name, statement
    return None


def _parameter(source: SourceText, kind: str, *, name: str, call: ast.Call) -> str:
    if kind == "ParamSpec":
        return f"**{name}"
    if kind == "TypeVarTuple":
        return f"*{name}"
    constraints = [source.segment(argument) for argument in call.args[1:]]
    if constraints:
        return f"{name}: ({', '.join(constraints)})"
    bound = next((k.value for k in call.keywords if k.arg == "bound"), None)
    return f"{name}: {source.segment(bound)}" if bound is not None else name


def _header(
    source: SourceText,
    node: ast.ClassDef,
    generic: ast.Subscript,
    *,
    parameters: list[str],
) -> TextEdit | None:
    """Add the parameters after the class name and drop the Generic base."""
    start = source.offset(node.lineno, node.col_offset)
    match = re.compile(rf"class\s+{re.escape(node.name)}\b").match(source.text, start)
    if match is None:
        return None
    name_end = match.end()
    listed = f"[{', '.join(parameters)}]"
    arguments: list[ast.expr | ast.keyword] = sorted(
        [*node.bases, *node.keywords], key=lambda item: source.span(item).start
    )
    base_start, base_end = source.span(generic)
    if len(arguments) == 1:
        opening = source.text[name_end:base_start]
        closing = source.text.find(")", base_end)
        if opening.strip() != "(" or source.text[base_end:closing].strip(", \t\n"):
            return None
        return TextEdit.at(source.text, TextSpan(name_end, closing + 1), listed)
    index = arguments.index(generic)
    if index + 1 < len(arguments):
        span = TextSpan(base_start, source.span(arguments[index + 1]).start)
    else:
        span = TextSpan(source.span(arguments[index - 1]).end, base_end)
    if "#" in source.text[span.start : span.end]:
        return None
    if source.text[name_end : source.span(arguments[0]).start].strip() != "(":
        return None
    # One edit keeps the insertion and the base removal from overlapping.
    return TextEdit.at(
        source.text,
        TextSpan(name_end, span.end),
        listed + source.text[name_end : span.start],
    )


def _unused_imports(
    source: SourceText,
    tree: ast.Module,
    *,
    removed: list[ast.AST],
    bindings: dict[str, str],
) -> Iterator[TextEdit]:
    """Remove Generic and declaration imports that only the removed code used."""
    unused: dict[int, list[ast.alias]] = {}
    statements: dict[int, ast.Import | ast.ImportFrom] = {}
    for local, qualified in bindings.items():
        if qualified.rpartition(".")[2] not in {"Generic", *_DECLARATIONS}:
            continue
        found = top_level_import(tree, local)
        if found is None or not isinstance(found[0], ast.ImportFrom):
            continue
        if name_used(tree, local, ignore=removed):
            continue
        statement, alias = found
        statements[id(statement)] = statement
        unused.setdefault(id(statement), []).append(alias)
    for key, aliases in unused.items():
        edit = remove_aliases(source, statements[key], aliases)
        if edit is not None:
            yield edit
