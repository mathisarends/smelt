"""Cache identity without importing the analysis engine or its schemas."""

import hashlib
import json
import sys
from enum import StrEnum
from functools import cache
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from typing import NamedTuple


class IdentityScope(StrEnum):
    """Which analyzer sources a cached value depends on."""

    COMPLETE = "complete"
    DECISIONS = "decisions"
    RULE_HOST = "rule-host"
    FACTS = "facts"


# The layers smelt.yml keeps each scope from importing, so editing them cannot
# change its cached values. Facts come from analysis, config and model alone.
# Rule decisions are made below the CLI. Any other source, including a
# directory added later, invalidates every scope.
_UNREACHABLE = {
    IdentityScope.COMPLETE: frozenset[str](),
    IdentityScope.DECISIONS: frozenset({"cli"}),
    # What a rule decision depends on besides the rules; those are hashed per rule.
    IdentityScope.RULE_HOST: frozenset({"cli", "rules"}),
    IdentityScope.FACTS: frozenset({"cli", "diagnostics", "engine", "rules"}),
}


class _RecordedFile(NamedTuple):
    path: str
    hash_mode: str
    hash_value: str
    size: int | None


def _installed_sources(package: Path) -> list[_RecordedFile] | None:
    """Use the installed wheel's build hashes, never those of another checkout."""
    try:
        installed = distribution("smelt-cli")
        direct = json.loads(installed.read_text("direct_url.json") or "{}")
        if direct.get("dir_info", {}).get("editable", False):
            return None
        if Path(str(installed.locate_file("smelt"))).resolve() != package:
            return None
        files = sorted(
            (
                file
                for file in installed.files or ()
                if file.parts
                and file.parts[0] == "smelt"
                and file.suffix in {".py", ".json"}
            ),
            key=str,
        )
        if not files or any(file.hash is None for file in files):
            return None
        return [
            _RecordedFile(str(file), file.hash.mode, file.hash.value, file.size)
            for file in files
            if file.hash is not None
        ]
    except (OSError, ValueError, TypeError, AttributeError, PackageNotFoundError):
        return None


def hashes_sources() -> bool:
    """Whether identities read the analyzer's sources, as editable checkouts do."""
    return _installed_sources(Path(__file__).resolve().parent.parent) is None


@cache
def analyzer_identity(scope: IdentityScope = IdentityScope.COMPLETE) -> str:
    """Invalidate editable checkouts too, even without a package version bump.

    A narrower scope leaves out the layers that cannot change its values, so
    editing the CLI keeps rule decisions and editing a rule keeps the facts.
    """
    unreachable = _UNREACHABLE[scope]
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent.parent
    record = _installed_sources(package)
    if record is not None:
        digest.update(b"wheel-record\0")
        record = [row for row in record if Path(row.path).parts[1] not in unreachable]
        digest.update(json.dumps(record).encode())
    else:
        digest.update(b"source\0")
        for path in sorted(
            path for path in package.rglob("*") if path.suffix in {".py", ".json"}
        ):
            relative = path.relative_to(package)
            if relative.parts[0] in unreachable:
                continue
            digest.update(relative.as_posix().encode())
            digest.update(path.read_bytes())
    digest.update(str(sys.version_info).encode())
    for dependency in ("smelt-cli", "grimp", "pydantic", "packaging", "pyyaml"):
        try:
            installed = version(dependency)
        except PackageNotFoundError:
            installed = "uninstalled"
        digest.update(f"{dependency}:{installed}".encode())
    return digest.hexdigest()
