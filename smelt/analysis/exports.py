"""Where a name that a module exposes is defined, following package re-exports.

``from app.billing import Invoice`` names ``app.billing``, but ``Invoice`` lives in
``app.billing.models`` when the facade ``app/billing/__init__.py`` re-exports it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.analysis.parsing import resolve_relative

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# Re-export chains are short; a longer one is more likely a cycle than a design.
MAX_HOPS = 8


@dataclass(frozen=True, slots=True)
class Namespace:
    """The names a module binds at top level."""

    module: str
    # local name -> the absolute name it was imported as
    imported: dict[str, str] = field(default_factory=dict)
    defined: frozenset[str] = frozenset()
    # defined name -> dotted names its value refers to: ``("chat", "feature")``
    references: dict[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)


type Loader = Callable[[str], "Namespace | None"]


def namespace_of(tree: ast.Module, module: str, *, is_package: bool) -> Namespace:
    imported: dict[str, str] = {}
    defined: set[str] = set()
    references: dict[str, tuple[tuple[str, ...], ...]] = {}
    for node in _top_level(tree.body):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = alias.name if alias.asname else name
                defined.discard(name)
                references.pop(name, None)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(
                module, is_package=is_package, level=node.level, target=node.module
            )
            for alias in node.names:
                if alias.name != "*":
                    qualified = f"{base}.{alias.name}" if base else alias.name
                    name = alias.asname or alias.name
                    imported[name] = qualified
                    defined.discard(name)
                    references.pop(name, None)
        else:
            for name in _defined_names(node):
                defined.add(name)
                imported.pop(name, None)
                references[name] = tuple(_dotted_names(node))
    return Namespace(module, imported, frozenset(defined), references)


def resolve_export(
    module: str, name: str, load: Loader
) -> tuple[str, str | None] | None:
    """The module defining ``module.name`` and its name there.

    ``(submodule, None)`` when the name is a module itself; None for names outside
    the loader's modules, star imports, and chains that cycle or run too long.
    """
    seen: set[tuple[str, str]] = set()
    while len(seen) < MAX_HOPS and (module, name) not in seen:
        seen.add((module, name))
        namespace = load(module)
        if namespace is None:
            return None
        if name in namespace.defined:
            return module, name
        target = namespace.imported.get(name) or f"{module}.{name}"
        if load(target) is not None:
            return target, None
        if target == f"{module}.{name}":
            return None
        module, _, name = target.rpartition(".")
        if not module:
            return None
    return None


def resolve_dotted(qualified: str, load: Loader) -> tuple[str, str | None] | None:
    """Like :func:`resolve_export` for a dotted name such as ``app.billing.Invoice``.

    The longest module prefix short of the last segment is imported as a module;
    the remaining segments are attributes, as in ``from app.billing import
    Invoice``, so a facade binding wins over a submodule of the same name. Access
    on anything but a module stops at that object, which is where it is defined.
    """
    parts = qualified.split(".")
    end = next(
        (
            end
            for end in range(len(parts) - 1, 0, -1)
            if load(".".join(parts[:end])) is not None
        ),
        None,
    )
    if end is None:
        return (qualified, None) if load(qualified) is not None else None
    current: tuple[str, str | None] | None = (".".join(parts[:end]), None)
    for attribute in parts[end:]:
        if current is None or current[1] is not None:
            break
        current = resolve_export(current[0], attribute, load)
    return current


def _top_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements at module level, including those under ``if`` and ``try``."""
    for node in body:
        if isinstance(node, ast.If):
            yield from _top_level(node.body)
            yield from _top_level(node.orelse)
        elif isinstance(node, ast.Try):
            for block in (node.body, node.orelse, node.finalbody):
                yield from _top_level(block)
            for handler in node.handlers:
                yield from _top_level(handler.body)
        else:
            yield node


def _defined_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign | ast.AugAssign):
        targets = [node.target]
    elif isinstance(node, ast.TypeAlias):
        targets = [node.name]
    return [
        name.id
        for target in targets
        for name in ast.walk(target)
        if isinstance(name, ast.Name)
    ]


def _dotted_names(node: ast.stmt) -> Iterator[tuple[str, ...]]:
    """``chat.feature`` -> ``("chat", "feature")`` for every name the value reads."""
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            parts: list[str] = []
            current: ast.expr = child
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                yield (current.id, *reversed(parts))
        elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            yield (child.id,)
