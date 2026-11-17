from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT367",))


def test_authentication_state_support_definitions_are_reported_once(
    tmp_path: Path,
) -> None:
    # Regression for https://github.com/mathisarends/smelt/issues/181.
    source = (
        "class AuthenticationState: pass\n"
        + "".join(f"class _Model{i}: pass\n" for i in range(9))
        + "".join(f"def _merge{i}(): pass\n" for i in range(10))
        + "_MAX_RECORDS = 100\n_MAX_BYTES: int = 1024\n"
    )
    root = _project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT367",
        "app/state.py",
        2,
        1,
    )
    assert finding.severity == Severity.HINT
    assert finding.expected is not None
    assert finding.expected["max_private_definitions"] == 12
    assert finding.expected["count"] == 19
    assert finding.expected["counts"] == {
        "classes": 9,
        "functions": 10,
        "type_aliases": 0,
    }
    definitions = finding.expected["definitions"]
    assert len(definitions) == 19
    assert definitions[0] == {
        "name": "_Model0",
        "kind": "class",
        "line": 2,
        "column": 1,
    }
    assert definitions[-1] == {
        "name": "_merge9",
        "kind": "function",
        "line": 20,
        "column": 1,
    }
    assert finding.expected["constant_count"] == 2
    assert finding.expected["constants"] == [
        {"name": "_MAX_RECORDS", "kind": "constant", "line": 21, "column": 1},
        {"name": "_MAX_BYTES", "kind": "constant", "line": 22, "column": 1},
    ]
    assert finding.expected["definition_edges"] == [
        *[f"class:_Model{i}" for i in range(9)],
        *[f"function:_merge{i}" for i in range(10)],
    ]
    assert finding.expected["automatic_rewrite"] is False
    assert (root / "app/state.py").read_text() == source


@pytest.mark.parametrize("limit", [0, 1, 12])
def test_allows_exactly_the_limit_and_reports_the_next_definition(
    tmp_path: Path, limit: int
) -> None:
    source = "".join(f"class _Model{i}: pass\n" for i in range(limit))
    root = _project(
        tmp_path, source, settings=f"structure: {{max_private_definitions: {limit}}}\n"
    )
    assert violations(root, SELECT) == []
    write_project(root, {"app/state.py": source + "class _Added: pass\n"})
    [finding] = violations(root, SELECT)
    assert finding.expected is not None
    assert finding.expected["count"] == limit + 1


def test_counts_async_functions_and_pep695_aliases_but_deduplicates_overloads(
    tmp_path: Path,
) -> None:
    source = """\
from typing import overload
@overload
def _read(value: str) -> str: ...
@overload
def _read(value: int) -> int: ...
def _read(value): return value
async def _fetch(): pass
type _Payload = str | bytes
class _Model: pass
_LIMIT = _OTHER = 1
_LIMIT = 2
__VERSION__ = '1'
_DECLARED: int
"""
    [finding] = violations(
        _project(
            tmp_path, source, settings="structure: {max_private_definitions: 0}\n"
        ),
        SELECT,
    )
    assert finding.expected is not None
    assert finding.expected["count"] == 4
    assert finding.expected["counts"] == {
        "classes": 1,
        "functions": 2,
        "type_aliases": 1,
    }
    assert finding.expected["definitions"] == [
        {"name": "_read", "kind": "function", "line": 3, "column": 1},
        {"name": "_fetch", "kind": "function", "line": 7, "column": 1},
        {"name": "_Payload", "kind": "type_alias", "line": 8, "column": 1},
        {"name": "_Model", "kind": "class", "line": 9, "column": 1},
    ]
    assert finding.expected["constant_count"] == 2


def test_ignores_imports_public_names_dunders_nested_and_guarded_definitions(
    tmp_path: Path,
) -> None:
    source = """\
from external import _Imported
from typing import TYPE_CHECKING
class Public:
    class _Nested: pass
    _LIMIT = 1
    def _method(self): pass
def public():
    class _Local: pass
    def _helper(): pass
    _LIMIT = 2
type PublicAlias = int
def __getattr__(name): pass
class __Special__: pass
type __Alias__ = int
_local = 1
_Alias = list[str]
if TYPE_CHECKING:
    class _TypeOnly: pass
if enabled:
    def _conditional(): pass
try:
    class _Optional: pass
except ImportError:
    pass
"""
    assert (
        violations(
            _project(
                tmp_path, source, settings="structure: {max_private_definitions: 0}\n"
            ),
            SELECT,
        )
        == []
    )


def test_constants_alone_never_exceed_the_definition_limit(tmp_path: Path) -> None:
    source = "".join(f"_LIMIT_{i} = {i}\n" for i in range(30))
    assert (
        violations(
            _project(
                tmp_path, source, settings="structure: {max_private_definitions: 0}\n"
            ),
            SELECT,
        )
        == []
    )


def test_checks_initializers_and_excludes_tests_and_excluded_sources(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "",
        settings="structure: {max_private_definitions: 0}\n",
    )
    write_project(
        root,
        {
            "app/__init__.py": "class _Model: pass\n",
            "tests/test_state.py": "class _TestModel: pass\n",
            "app/excluded.py": "class _Excluded: pass\n",
            "smelt.yml": "version: 1\nproject:\n  root_packages: [app]\n  exclude:\n    - path: app/excluded.py\n      reason: outside this check\nstructure: {max_private_definitions: 0}\n",
        },
    )
    [finding] = violations(root, SELECT)
    assert finding.path == "app/__init__.py"


def test_global_and_closest_package_limits_including_zero(tmp_path: Path) -> None:
    source = "class _Local: pass\n"
    root = _project(
        tmp_path,
        source,
        settings="""\
rules: {visibility/excess-private-definitions: warning}
structure:
  max_private_definitions: 1
  contracts:
    outer: {match: app, max_private_definitions: 0}
    inherited: {match: app.legacy, module_docstrings: allow}
    inner: {match: app.legacy.models, max_private_definitions: 1}
""",
    )
    write_project(
        root,
        {
            "app/legacy/__init__.py": "",
            "app/legacy/state.py": source,
            "app/legacy/models/__init__.py": "",
            "app/legacy/models/state.py": source,
        },
    )
    found = violations(root)
    assert {(v.path, v.severity) for v in found} == {
        ("app/state.py", Severity.WARNING),
        ("app/legacy/state.py", Severity.WARNING),
    }
    assert all(
        v.expected is not None and v.expected["max_private_definitions"] == 0
        for v in found
    )
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + "rules: {visibility/excess-private-definitions: hint}\nstructure: {max_private_definitions: 1}\n"
        },
    )
    assert violations(root) == []
    assert violations(root, CheckOptions(use_cache=False)) == []


def test_requires_opt_in_and_supports_off_overrides_and_suppression(
    tmp_path: Path,
) -> None:
    source = "class _Local: pass\n"
    root = _project(
        tmp_path, source, settings="structure: {max_private_definitions: 0}\n"
    )
    assert violations(root) == []
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {visibility/excess-private-definitions: warning}
structure:
  max_private_definitions: 0
  contracts:
    quiet: {match: app.quiet, rules: {visibility/excess-private-definitions: off}}
""",
            "app/quiet/__init__.py": source,
            "app/intentional.py": "class _Local: pass  # smelt: ignore[SMT367] -- cohesive parser implementation\n",
        },
    )
    [finding] = violations(root)
    assert finding.path == "app/state.py"
    assert violations(root, CheckOptions(use_cache=False)) == [finding]


@pytest.mark.parametrize(
    "setting",
    [
        "structure: {max_private_definitions: -1}",
        "structure: {contracts: {bad: {match: app, max_private_definitions: -1}}}",
    ],
)
def test_rejects_negative_limits(tmp_path: Path, setting: str) -> None:
    with pytest.raises(ConfigError, match="max_private_definitions"):
        violations(_project(tmp_path, "", settings=setting))


def _project(tmp_path: Path, source: str, *, settings: str = "") -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + settings,
            "app/__init__.py": "",
            "app/state.py": source,
        },
    )
