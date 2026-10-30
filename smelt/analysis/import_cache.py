"""Per-module native import scans, keyed by contents and resolution inventory."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from grimp.application.ports.caching import Cache, CacheMiss
from grimp.domain.valueobjects import DirectImport, Module
from pydantic import TypeAdapter

from smelt.analysis.files import python_files

if TYPE_CHECKING:
    from grimp.application.ports.modulefinder import ModuleFile

    from smelt.analysis.cache import FactCache
    from smelt.analysis.files import FileIndex
    from smelt.analysis.parsing import AstCache

_IMPORTS = TypeAdapter(list[DirectImport])


def resolution_identity(files: FileIndex) -> str:
    inventory = files.store.inventory()
    paths = (
        inventory
        if inventory is not None
        else (
            path
            for roots in files.package_roots.values()
            for root in roots
            for path in python_files(root, store=files.store)
        )
    )
    inputs = [
        sorted(files.relative(path) for path in paths if path.suffix == ".py"),
        [
            (s.module, s.path, s.is_package)
            for s in sorted(files.sources.values(), key=lambda s: s.path)
        ],
        [
            (name, [(p.path, p.has_init) for p in parts])
            for name, parts in sorted(files.package_parts.items())
        ],
    ]
    return hashlib.sha256(json.dumps(inputs).encode()).hexdigest()


def import_cache_type(
    files: FileIndex, asts: AstCache, cache: FactCache | None, inventory: str
) -> type[Cache]:
    misses: set[Module] = set()

    class ContentCache(Cache):
        def key(self, module: Module) -> str:
            source = files.sources[module.name]
            if cache is None:
                raise CacheMiss
            return cache.key(
                "native-imports",
                [
                    source.module,
                    source.path,
                    source.is_package,
                    files.content_id(source.path),
                    inventory,
                    self.include_external_packages,
                    self.exclude_type_checking_imports,
                ],
            )

        def read_imports(self, module_file: ModuleFile) -> set[DirectImport]:
            module = module_file.module
            cached = (
                cache.read(self.key(module), _IMPORTS) if cache is not None else None
            )
            if cached is not None:
                return set(cached)
            # Validate only new contents before Grimp's native scanner, retaining
            # Smelt's syntax/encoding errors without reparsing unchanged files.
            asts.parse(files.sources[module.name].path)
            misses.add(module)
            raise CacheMiss

        def write(self, imports_by_module: dict[Module, set[DirectImport]]) -> None:
            if cache is not None:
                for module in misses:
                    cache.write(
                        self.key(module), sorted(imports_by_module[module]), _IMPORTS
                    )

        @classmethod
        def cache_dir_or_default(cls, cache_dir: str | None) -> str:
            return cache_dir or "smelt"

    return ContentCache
