"""One identity per rule implementation, so a rule edit keeps other rules' decisions."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.analysis.identity import IdentityScope, analyzer_identity, hashes_sources
from smelt.analysis.parsing import resolve_relative, tree_nodes

if TYPE_CHECKING:
    from smelt.analysis.cache import FactCache
    from smelt.rules.base import RuleContract

_IDENTITIES = TypeAdapter(dict[str, str])


def implementation_identity(rule: RuleContract, cache: FactCache | None) -> str:
    """Everything that can change ``rule``'s decisions, short of the checked project.

    An installed wheel has one identity for all rules. A source checkout hashes
    the rule's module with the rule modules it imports, the shared modules and
    catalog at the top of the rules package, and every layer below the rules.
    """
    complete = analyzer_identity(IdentityScope.DECISIONS)
    if not hashes_sources():
        return complete
    module = getattr(rule, "implementation_module", type(rule).__module__)
    return _identities(complete, cache).get(module, complete)


# The identities of one analyzer build; a worker restarts when its sources change.
_KNOWN: dict[str, dict[str, str]] = {}


def _identities(complete: str, cache: FactCache | None) -> dict[str, str]:
    known = _KNOWN.get(complete)
    if known is None:
        key = cache.decision_key("rule-identities", []) if cache else None
        known = cache.read(key, _IDENTITIES) if cache and key else None
        if known is None:
            known = _computed()
            if cache and key:
                cache.write(key, known, _IDENTITIES)
        _KNOWN.clear()
        _KNOWN[complete] = known
    return known


def _computed() -> dict[str, str]:
    rules = Path(__file__).resolve().parent
    sources = {_module(path, rules): path for path in sorted(rules.rglob("*.py"))}
    digests = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sources.items()
    }
    shared = sorted(name for name, path in sources.items() if path.parent == rules)
    base = [
        analyzer_identity(IdentityScope.RULE_HOST),
        hashlib.sha256((rules / "catalog.json").read_bytes()).hexdigest(),
        [(name, digests[name]) for name in shared],
    ]
    imports = {
        name: _imports(name, path, sources)
        for name, path in sources.items()
        if name not in shared
    }
    identities: dict[str, str] = {}
    for name in sources:
        own = sorted(_closure(name, imports) - set(shared))
        payload = json.dumps([base, [(module, digests[module]) for module in own]])
        identities[name] = hashlib.sha256(payload.encode()).hexdigest()
    return identities


def _module(path: Path, rules: Path) -> str:
    relative = path.relative_to(rules.parent.parent).with_suffix("")
    parts = relative.parts[:-1] if path.name == "__init__.py" else relative.parts
    return ".".join(parts)


def _imports(name: str, path: Path, sources: dict[str, Path]) -> set[str]:
    """Rule modules that importing ``name`` executes, wherever the import is written."""
    is_package = path.name == "__init__.py"
    candidates = [name]
    for node in tree_nodes(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            origin = resolve_relative(
                name, is_package=is_package, level=node.level, target=node.module
            )
            candidates.append(origin)
            candidates.extend(f"{origin}.{alias.name}" for alias in node.names)
    found: set[str] = set()
    for candidate in candidates:
        parts = candidate.split(".")
        # Importing a submodule runs every package above it first.
        found.update(
            prefix
            for end in range(1, len(parts) + 1)
            if (prefix := ".".join(parts[:end])) in sources
        )
    return found - {name}


def _closure(name: str, imports: dict[str, set[str]]) -> set[str]:
    seen = {name}
    pending = [name]
    while pending:
        for imported in imports.get(pending.pop(), ()):
            if imported not in seen:
                seen.add(imported)
                pending.append(imported)
    return seen
