from __future__ import annotations

import difflib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext
from smelt.cli.support import EXIT_OK, CliError
from smelt.config import load_config
from smelt.config.loader import discover_config
from smelt.config.source import SourceStore
from smelt.engine.migration import enable_rules, migrate_policy
from smelt.engine.rule_settings import RuleSettings
from smelt.rules.registry import build_rule_set

if TYPE_CHECKING:
    import argparse

    from smelt.cli.support import Console


class _Preview(SourceStore):
    def __init__(self, path: Path, content: bytes) -> None:
        self._path = path
        self._content = content

    def read_bytes(self, path: Path) -> bytes:
        return self._content if path == self._path else super().read_bytes(path)


def config_migrate(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    path = (cwd / args.config).resolve() if args.config else discover_config(cwd)
    if path is None:
        raise CliError(
            "no smelt.yml found in this directory or its parents", kind="config"
        )
    original = path.read_bytes()
    encoding = "utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8"
    before = original.decode(encoding)
    result = migrate_policy(before)
    rules = build_rule_set(lazy=True)
    loaded = load_config(path, store=_Preview(path, result.text.encode(encoding)))
    enabled = enable_rules(
        result.text,
        [
            (location, rule.name, rule.default_severity.value)
            for rule, locations in RuleSettings(rules, loaded).policy_enablements()
            for location in locations
        ],
    )
    after = enabled.text
    if "\r\n" in before and "\n" not in before.replace("\r\n", ""):
        after = after.replace("\r\n", "\n").replace("\n", "\r\n")
    for note in (*result.notes, *enabled.notes):
        console.print(note)
    if after != before:
        console.print(
            "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=path.name,
                    tofile=path.name,
                )
            ),
            end="",
        )
    content = after.encode(encoding)
    store = _Preview(path, content)
    loaded = load_config(path, store=store)
    policy = RuleSettings(rules, loaded)
    policy.validate_packages(AnalysisContext(loaded.root, loaded.config, store=store))
    if after == before:
        console.print("Configuration already uses the current keys and selectors.")
    elif args.write:
        _replace(path, original, content)
    return EXIT_OK


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
