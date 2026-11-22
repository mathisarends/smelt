from __future__ import annotations

import difflib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext
from smelt.cli.support import EXIT_OK, CliError
from smelt.config import ConfigError, load_config
from smelt.config.loader import config_files, discover_config, raw_config
from smelt.config.source import SourceStore
from smelt.engine.migration import (
    LayerModuleMigration,
    Migration,
    RuleEnablement,
    enable_rules,
    migrate_policy,
    project_limit_rules,
)
from smelt.engine.rule_settings import RuleSettings
from smelt.rules.registry import build_rule_set

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping

    from smelt.cli.support import Console


class _Preview(SourceStore):
    def __init__(self, contents: Mapping[Path, bytes]) -> None:
        self._contents = contents

    def read_bytes(self, path: Path) -> bytes:
        content = self._contents.get(path)
        return content if content is not None else super().read_bytes(path)


def config_migrate(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    path = (cwd / args.config).resolve() if args.config else discover_config(cwd)
    if path is None:
        raise CliError(
            "no smelt.yml found in this directory or its parents", kind="config"
        )
    # A version 2 file cannot load beside version 1 bases or members, so all migrate together.
    files = _migrate_files(path)
    rules = build_rule_set(lazy=True)
    loaded = load_config(path, store=_preview(files))
    # A broad selector can depend on an implicit activation we have yet to name.
    settings = RuleSettings(rules, loaded, validate=False)
    root = files.get(loaded.path) or files.setdefault(loaded.path, _File(loaded.path))
    root.apply(
        enable_rules(
            root.after,
            [
                RuleEnablement(
                    location, rule.name, settings.migration_severity(rule, location)
                )
                for rule, locations in settings.policy_enablements()
                for location in locations
            ],
        )
    )
    base = loaded.root
    for file in files.values():
        label = _label(file.path, base)
        for note in file.notes:
            console.print(f"{label}: {note}" if len(files) > 1 else note)
        if file.changed:
            console.print(
                "".join(
                    difflib.unified_diff(
                        file.before.splitlines(keepends=True),
                        file.after.splitlines(keepends=True),
                        fromfile=label,
                        tofile=label,
                    )
                ),
                end="",
            )
    store = _preview(files)
    loaded = load_config(path, store=store)
    policy = RuleSettings(rules, loaded)
    policy.validate_packages(AnalysisContext(loaded.root, loaded.config, store=store))
    changed = [file for file in files.values() if file.changed]
    if not changed:
        console.print("Configuration already uses the current keys and selectors.")
    elif args.write:
        for file in changed:
            _replace(file.path, file.original, file.content)
    return EXIT_OK


class _File:
    """One config file of the migration, keeping its encoding and line endings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.original = path.read_bytes()
        self._encoding = (
            "utf-8-sig" if self.original.startswith(b"\xef\xbb\xbf") else "utf-8"
        )
        self.before = self.original.decode(self._encoding)
        self.after = self.before
        self.notes: list[str] = []

    def apply(self, migration: Migration) -> None:
        self.after = migration.text
        self.notes.extend(migration.notes)

    @property
    def content(self) -> bytes:
        after = self.after
        if "\r\n" in self.before and "\n" not in self.before.replace("\r\n", ""):
            after = after.replace("\r\n", "\n").replace("\n", "\r\n")
        return after.encode(self._encoding)

    @property
    def changed(self) -> bool:
        return self.content != self.original


def _migrate_files(path: Path) -> dict[Path, _File]:
    inputs = config_files(path)
    files = {source: _File(source) for source in (*inputs.project, *inputs.members)}
    project_limits = project_limit_rules(
        files[source].before for source in inputs.project
    )
    bases = set(inputs.project[:-1])
    inherited_rules: frozenset[str] = frozenset()
    original = _preview(files)
    project_layers = LayerModuleMigration()
    for source, file in files.items():
        layers = project_layers if source in inputs.project else LayerModuleMigration()
        try:
            file.apply(
                layers.migrate(
                    file.before,
                    raw_config(source, store=original),
                    final=source not in bases,
                )
            )
        except ConfigError as exc:
            raise ConfigError(
                exc.issues, _label(source, inputs.project[-1].parent)
            ) from exc
        migration = migrate_policy(
            file.after,
            inherited_rules=inherited_rules,
            project_limits=project_limits if source in bases else frozenset(),
        )
        file.apply(migration)
        if source in inputs.project:
            inherited_rules = migration.configured_rules
    return files


def _preview(files: Mapping[Path, _File]) -> _Preview:
    return _Preview({path: file.content for path, file in files.items()})


def _label(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name


def _replace(path: Path, original: bytes, replacement: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
    try:
        temporary.write_bytes(replacement)
        temporary.chmod(path.stat().st_mode)
        if path.read_bytes() != original:
            raise CliError(
                "configuration changed while migrating; run the command again",
                kind="config",
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
