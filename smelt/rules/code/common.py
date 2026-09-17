import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.syntax import ClassInfo, ModuleSyntax
    from smelt.diagnostics.violation import Violation
    from smelt.rules.base import BaseRule

type FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class Scope:
    function: FunctionNode
    qualname: str  # ``Class.method`` or ``function``
    cls: ast.ClassDef | None


def node_violation(  # noqa: PLR0913
    rule: BaseRule,
    ctx: AnalysisContext,
    syntax: ModuleSyntax,
    node: ast.expr | ast.stmt,
    message: str,
    *,
    target_module: str | None = None,
    expected: dict[str, object] | None = None,
    hint: str | None = None,
) -> Violation:
    info = ctx.model.info(syntax.module)
    end_line = node.end_lineno or node.lineno
    end_column = (node.end_col_offset or node.col_offset) + 1
    if end_line != node.lineno:
        end_line, end_column = (
            node.lineno,
            len(ctx.files.line(syntax.path, node.lineno)) + 1,
        )
    return rule.violation(
        message,
        path=syntax.path,
        line=node.lineno,
        column=node.col_offset + 1,
        end_line=end_line,
        end_column=end_column,
        source_module=syntax.module,
        target_module=target_module,
        feature=info.feature if info else None,
        layer=info.layer if info else None,
        expected=expected,
        hint=hint,
    )


def scopes(tree: ast.Module) -> Iterator[Scope]:
    """Functions and methods (including nested ones) with a readable qualname."""
    stack: list[tuple[ast.AST, str, ast.ClassDef | None]] = [(tree, "", None)]
    while stack:
        node, prefix, cls = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                stack.append((child, f"{prefix}{child.name}.", child))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{prefix}{child.name}"
                yield Scope(child, qualname, cls)
                stack.append((child, f"{qualname}.", None))
            elif not isinstance(child, ast.expr):
                stack.append((child, prefix, cls))


def annotation_names(annotation: ast.expr | None) -> Iterator[ast.expr]:
    """``Name``/``Attribute`` nodes inside an annotation, including string annotations."""
    if annotation is None:
        return
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name | ast.Attribute):
            yield node
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            parsed = parse_annotation(node.value)
            if parsed is not None:
                for inner in annotation_names(parsed):
                    yield ast.copy_location(inner, node)


def parse_annotation(text: str) -> ast.expr | None:
    try:
        return ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return None


def implementation_classes(ctx: AnalysisContext) -> dict[str, tuple[str, ClassInfo]]:
    """Classes with a role that implements another role: qualname -> (role, class)."""
    implementations = {
        name: role.detect.implements
        for name, role in ctx.config.roles.items()
        if role.detect.implements
    }
    if not implementations:
        return {}
    found: dict[str, tuple[str, ClassInfo]] = {}
    for match in ctx.roles.matches():
        if match.role in implementations and match.cls.qualname not in found:
            found[match.cls.qualname] = (match.role, match.cls)
    return found


def implemented_ports(ctx: AnalysisContext, role: str, cls: ClassInfo) -> list[str]:
    target = ctx.config.roles[role].detect.implements
    return [
        ancestor
        for ancestor in ctx.syntax.ancestors(cls)
        if target is not None and target in ctx.roles.roles_of(ancestor)
    ]
