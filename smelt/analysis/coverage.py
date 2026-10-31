"""An inventory independent of configured package selection and import rules."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter

from smelt.analysis.errors import AnalysisError
from smelt.analysis.files import python_files
from smelt.analysis.workspace import import_root, workspace_members
from smelt.config.models import SourceExclusion
from smelt.config.patterns import path_matches
from smelt.model import ModuleKind

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.files import FileIndex, SourceFile
    from smelt.analysis.parsing import AstCache
    from smelt.model import ArchitectureModel, ModuleInfo

type CoverageStatus = Literal[
    "governed", "facade", "unclassified", "uncovered", "excluded"
]
_BOOL = TypeAdapter(bool)


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    path: str
    module: str | None
    status: CoverageStatus
    kind: str | None = None
    feature: str | None = None
    layer: str | None = None
    wiring: bool = False
    central: bool = False
    reason: str | None = None
    policies: tuple[str, ...] = ()
    source_root: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "module": self.module,
            "status": self.status,
            "kind": self.kind,
            "feature": self.feature,
            "layer": self.layer,
            "wiring": self.wiring,
            "central": self.central,
            "reason": self.reason,
            "policies": list(self.policies),
            "source_root": self.source_root,
        }


@dataclass(frozen=True)
class CoverageIndex:
    entries: tuple[CoverageEntry, ...]
    scan_roots: tuple[str, ...]
    unexplained_exclusions: tuple[str, ...]
    discovery_errors: tuple[str, ...] = ()

    @classmethod
    def build(
        cls, files: FileIndex, model: ArchitectureModel, asts: AstCache
    ) -> CoverageIndex:
        roots = {
            files.resolve_path(files.root / value)
            for value in files.config.project.source_roots
        }
        test_roots = {
            files.resolve_path(files.root / value)
            for value in files.config.project.test_roots
        }
        discovery_errors: tuple[str, ...] = ()
        try:
            members = workspace_members(files.root, strict=True, store=files.store)
        except AnalysisError as exc:
            if files.config.project.coverage == "strict":
                raise
            members = []
            discovery_errors = (str(exc),)
        for member in members:
            directory = files.root / member.path
            if not files.store.is_dir(directory):
                continue
            # uv excludes control packaging, not architecture coverage. Inspect
            # their Python files too, unless the policy explicitly excludes them.
            located = import_root(directory, store=files.store)
            fallback = (
                directory / "src"
                if files.store.is_dir(directory / "src")
                else directory
            )
            roots.add(files.resolve_path(located[0] if located else fallback))
            test_roots.update(
                files.resolve_path(directory / name) for name in ("tests", "test")
            )
        inventory = {
            source.path: _classify_source(source, files, model, asts)
            for source in files.sources.values()
        }
        # The most specific import root names a module in nested workspaces.
        ordered = sorted(roots, key=lambda value: (-len(value.parts), value.as_posix()))
        for directory in ordered:
            if not files.store.is_dir(directory):
                continue
            for path in python_files(directory, store=files.store):
                absolute = files.resolve_path(path)
                relative = files.relative(path)
                if relative in inventory or any(
                    absolute.is_relative_to(root) for root in test_roots
                ):
                    continue
                exclusion = next(
                    (
                        entry
                        for entry in files.config.project.exclude
                        if path_matches(
                            entry if isinstance(entry, str) else entry.path, relative
                        )
                    ),
                    None,
                )
                module = _module_name(path, directory)
                if exclusion is not None:
                    reason = (
                        exclusion.reason
                        if isinstance(exclusion, SourceExclusion)
                        else None
                    )
                    inventory[relative] = CoverageEntry(
                        relative,
                        module,
                        "excluded",
                        reason=reason,
                        source_root=files.relative(directory),
                    )
                else:
                    inventory[relative] = CoverageEntry(
                        relative,
                        module,
                        "uncovered",
                        reason="Not selected by project.root_packages/source_roots, or not an importable module.",
                        source_root=files.relative(directory),
                    )
        return cls(
            tuple(inventory[path] for path in sorted(inventory)),
            tuple(sorted(files.relative(path) for path in roots)),
            tuple(
                entry
                for entry in files.config.project.exclude
                if isinstance(entry, str)
            ),
            discovery_errors,
        )

    @property
    def gaps(self) -> tuple[CoverageEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.status in ("unclassified", "uncovered")
        )

    @property
    def complete(self) -> bool:
        return (
            not self.gaps
            and not self.unexplained_exclusions
            and not self.discovery_errors
        )

    def for_path(self, path: str) -> CoverageEntry | None:
        return next((entry for entry in self.entries if entry.path == path), None)

    def summary(self) -> dict[str, Any]:
        counts = Counter(entry.status for entry in self.entries)
        return {
            "complete": self.complete,
            "counts": {
                status: counts[status]
                for status in (
                    "governed",
                    "facade",
                    "unclassified",
                    "uncovered",
                    "excluded",
                )
            },
            "scan_roots": list(self.scan_roots),
            "unexplained_exclusions": list(self.unexplained_exclusions),
            "discovery_errors": list(self.discovery_errors),
        }


def is_governed(info: ModuleInfo | None, *, layers_declared: bool = True) -> bool:
    return bool(
        info
        and (
            info.layer
            or (info.feature is not None and not layers_declared)
            or info.wiring
            or info.kind in (ModuleKind.SHARED, ModuleKind.COMPOSITION_ROOT)
        )
    )


def _classify_source(
    source: SourceFile, files: FileIndex, model: ArchitectureModel, asts: AstCache
) -> CoverageEntry:
    info = model.info(source.module)
    status: CoverageStatus = "unclassified"
    reason = (
        "No architecture layer or explicit shared, wiring or composition-root policy."
    )
    if is_governed(info, layers_declared=bool(model.layers)):
        status, reason = "governed", "Explicit architecture policy."
    elif source.is_package and _is_facade(source, asts):
        status, reason = (
            "facade",
            "Package facade containing imports, documentation and package metadata only.",
        )
    return CoverageEntry(
        source.path,
        source.module,
        status,
        kind=info.kind.value if info else None,
        feature=info.feature if info else None,
        layer=info.layer if info else None,
        wiring=bool(info and info.wiring),
        central=bool(info and info.central),
        reason=reason,
        policies=_policies(info),
        source_root=files.source_root_dir(source.module),
    )


def _policies(info: ModuleInfo | None) -> tuple[str, ...]:
    if info is None:
        return ()
    policies = []
    if info.layer:
        policies.append(f"architecture.layers.{info.layer}")
    if info.central:
        policies.append("architecture.modules")
    if info.feature:
        policies.append("architecture.features")
    if info.wiring:
        policies.append("architecture.wiring")
    if info.kind is ModuleKind.SHARED:
        policies.append("architecture.shared")
    if info.kind is ModuleKind.COMPOSITION_ROOT:
        policies.append("architecture.composition_root")
    return tuple(policies)


def _is_facade(source: SourceFile, asts: AstCache) -> bool:
    if asts.cache is not None:
        return asts.cache.get(
            "facade",
            [source.path, asts.files.content_id(source.path)],
            _BOOL,
            lambda: _facade_syntax(source, asts),
        )
    return _facade_syntax(source, asts)


def _facade_syntax(source: SourceFile, asts: AstCache) -> bool:
    try:
        tree = asts.parse(source.path)
    except AnalysisError:
        return False
    version_calls = _version_calls(tree)
    return all(_facade_statement(node, version_calls) for node in tree.body)


def _version_calls(tree: ast.Module) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib.metadata":
            calls.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "version"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            calls.update(
                f"{alias.asname or alias.name}.version"
                for alias in node.names
                if alias.name == "metadata"
            )
        elif isinstance(node, ast.Import):
            calls.update(
                f"{alias.asname or alias.name}.version"
                for alias in node.names
                if alias.name == "importlib.metadata"
            )
    return calls


def _facade_statement(node: ast.stmt, version_calls: set[str]) -> bool:
    if isinstance(node, ast.Import | ast.ImportFrom | ast.Pass):
        return True
    if isinstance(node, ast.Expr):
        return isinstance(node.value, ast.Constant) and isinstance(
            node.value.value, str
        )
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return _metadata_assignment(node, version_calls)
    if isinstance(node, ast.If) and ast.unparse(node.test) in (
        "TYPE_CHECKING",
        "typing.TYPE_CHECKING",
    ):
        return all(
            _facade_statement(child, version_calls)
            for child in (*node.body, *node.orelse)
        )
    if isinstance(node, ast.Try):
        blocks = [
            node.body,
            node.orelse,
            node.finalbody,
            *(handler.body for handler in node.handlers),
        ]
        safe_handlers = all(
            handler.type is None or isinstance(handler.type, ast.Name | ast.Attribute)
            for handler in node.handlers
        )
        return safe_handlers and all(
            _facade_statement(child, version_calls)
            for block in blocks
            for child in block
        )
    return False


def _metadata_assignment(
    node: ast.Assign | ast.AnnAssign, version_calls: set[str]
) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names = [target.id for target in targets if isinstance(target, ast.Name)]
    if len(names) != len(targets) or not set(names) <= {"__all__", "__version__"}:
        return False
    if node.value is None:
        return False
    try:
        ast.literal_eval(node.value)
    except (ValueError, TypeError):
        value = node.value
        return (
            names == ["__version__"]
            and isinstance(value, ast.Call)
            and ast.unparse(value.func) in version_calls
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
            and not value.keywords
        )
    return True


def _module_name(path: Path, root: Path) -> str | None:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return (
        ".".join(parts)
        if parts and all(part.isidentifier() for part in parts)
        else None
    )
