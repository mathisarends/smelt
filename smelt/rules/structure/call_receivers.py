from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.bindings import binding_counts, qualified_name
from smelt.analysis.context import Index
from smelt.analysis.exports import namespace_of
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.files import SourceFile
    from smelt.diagnostics.violation import Violation


class InlineConstructorReceiver(Rule):
    file_local = True
    code = "CPX008"
    name = "complexity/inline-constructor-receiver"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A method is called directly on a newly constructed instance.",
        rationale=(
            "Naming the instance on its own line makes it easier to distinguish "
            "construction from the operation that follows. This opt-in review "
            "checks direct method calls used as the whole value of an assignment, "
            "return or expression statement, including an awaited call. It "
            "recognizes unshadowed top-level class declarations and unshadowed "
            "imports whose final name starts with a capital letter (ignoring "
            "leading underscores). The import convention is a heuristic and can "
            "also match a callable with a class-like name. The rule skips files "
            "with wildcard imports. Bare builtins, unresolved names, lowercase "
            "factory imports, chains of method calls, nested expressions and "
            "test files are outside its scope."
        ),
        bad=(
            "from plugins import PointerTools\n\n"
            "pointer = PointerTools().tool(\n"
            "    PointerTools.drag_at,\n"
            '    description="Press, move, release.",\n'
            ")"
        ),
        good=(
            "from plugins import PointerTools\n\n"
            "pointer_tools = PointerTools()\n"
            "pointer = pointer_tools.tool(\n"
            "    PointerTools.drag_at,\n"
            '    description="Press, move, release.",\n'
            ")"
        ),
        fix=(
            "Assign the constructor call to a descriptive variable before the "
            "statement and use that variable as the method receiver. Review "
            "whether keeping the instance bound affects its lifetime. No "
            "automatic rewrite is performed. Enable "
            "rules: {complexity/inline-constructor-receiver: warning} globally "
            "or in a package override, or select CPX008."
        ),
        canonical=(
            "Bind a newly constructed instance to a name before calling methods on it"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            for constructor, method in _constructor_calls(tree, source):
                name = ast.unparse(constructor.func)
                yield self.violation(
                    f"Name the {name} instance before calling .{method}()",
                    path=source.path,
                    line=constructor.lineno,
                    column=constructor.col_offset + 1,
                    end_line=constructor.end_lineno,
                    end_column=(constructor.end_col_offset or constructor.col_offset)
                    + 1,
                    source_module=source.module,
                    expected={"constructor": name, "method": method},
                    hint=(
                        "Assign the constructor call to a descriptive variable "
                        "and call the method on that variable."
                    ),
                )


def _constructor_calls(
    tree: ast.Module, source: SourceFile
) -> Iterator[tuple[ast.Call, str]]:
    candidates = list(_direct_receivers(tree))
    if not candidates:
        return
    counts = binding_counts(tree)
    if counts["*"]:
        return
    classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and counts[node.name] == 1
    }
    namespace = namespace_of(tree, source.module, is_package=source.is_package)
    imports = {
        local: target
        for local, target in namespace.imported.items()
        if counts[local] == 1
    }
    for receiver, method in candidates:
        callee = receiver.func
        if isinstance(callee, ast.Name) and callee.id in classes:
            yield receiver, method
            continue
        if qualified := qualified_name(callee, imports):
            name = qualified.rsplit(".", 1)[-1].lstrip("_")
            if name[:1].isupper():
                yield receiver, method


def _direct_receivers(tree: ast.Module) -> Iterator[tuple[ast.Call, str]]:
    for node in ast.walk(tree):
        match node:
            case (
                ast.Assign(value=value)
                | ast.AnnAssign(value=value)
                | ast.Return(value=value)
                | ast.Expr(value=value)
            ):
                call = value.value if isinstance(value, ast.Await) else value
                match call:
                    case ast.Call(
                        func=ast.Attribute(value=ast.Call() as receiver, attr=method)
                    ):
                        yield receiver, method
