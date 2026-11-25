"""The top-level symbols of modules, as the native core reads them for `smelt init`.

``from app.billing import Invoice`` names ``app.billing``, but ``Invoice`` lives in
``app.billing.models`` when the facade ``app/billing/__init__.py`` re-exports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from smelt.analysis import native
from smelt.analysis.errors import AnalysisError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

# Re-export chains are short; a longer one is more likely a cycle than a design.
_MAX_HOPS = 8


@dataclass(frozen=True, slots=True)
class Namespace:
    """The names a module binds at top level."""

    module: str
    # local name -> the absolute name it was imported as
    imported: dict[str, str]
    defined: frozenset[str]
    # defined name -> dotted names its value refers to: ``("chat", "feature")``
    references: dict[str, tuple[tuple[str, ...], ...]]


@dataclass(frozen=True, slots=True)
class ImportStatement:
    """``import names`` or ``from <level dots><module> import names``."""

    from_import: bool
    level: int
    module: str | None
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModuleSymbols:
    namespace: Namespace
    # Every import statement, including those inside functions and classes.
    imports: tuple[ImportStatement, ...]
    # Every string constant, in source order.
    strings: tuple[str, ...]


type Loader = Callable[[str], Namespace | None]


def read_symbols(files: Sequence[tuple[Path, str]]) -> dict[Path, ModuleSymbols | None]:
    """The symbols of each ``(path, module)``, None for a file that does not parse."""
    report = native.request(
        "--internal-symbols",
        {
            "files": [
                {
                    "path": str(path),
                    "module": module,
                    "is_package": path.name == "__init__.py",
                }
                for path, module in files
            ]
        },
    )
    if "error" in report:
        raise AnalysisError(report["error"])
    return {
        path: None if found is None else _module_symbols(module, found)
        for (path, module), found in zip(files, report["symbols"], strict=True)
    }


def resolve_dotted(qualified: str, load: Loader) -> tuple[str, str | None] | None:
    """The module defining a dotted name such as ``app.billing.Invoice``.

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
        current = _resolve_export(current[0], attribute, load)
    return current


def _module_symbols(module: str, found: dict[str, Any]) -> ModuleSymbols:
    namespace = Namespace(
        module,
        found["imported"],
        frozenset(found["defined"]),
        {
            name: tuple(tuple(dotted) for dotted in dotted_names)
            for name, dotted_names in found["references"].items()
        },
    )
    imports = tuple(
        ImportStatement(
            from_import=entry["from"],
            level=entry["level"],
            module=entry["module"],
            names=tuple(entry["names"]),
        )
        for entry in found["imports"]
    )
    return ModuleSymbols(namespace, imports, tuple(found["strings"]))


def _resolve_export(
    module: str, name: str, load: Loader
) -> tuple[str, str | None] | None:
    """The module defining ``module.name`` and its name there.

    ``(submodule, None)`` when the name is a module itself; None for names outside
    the loader's modules, star imports, and chains that cycle or run too long.
    """
    # Names are single segments, so ``module.name`` identifies the pair.
    seen: set[str] = set()
    while len(seen) < _MAX_HOPS and f"{module}.{name}" not in seen:
        seen.add(f"{module}.{name}")
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
