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
SELECT = CheckOptions(select=("SMT364",))
SOURCE = """\
class Client:
    def __init__(self, values):
        for value in values:
            if not value.ready:
                raise ValueError('not ready')
        self.values = values
"""


def test_reports_one_method_with_counted_node_locations(tmp_path: Path) -> None:
    root = _project(tmp_path, SOURCE)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT364",
        "app/model.py",
        2,
        5,
    )
    assert finding.severity == Severity.HINT
    assert finding.expected is not None
    assert {
        key: value for key, value in finding.expected.items() if key != "decision_edges"
    } == {
        "class": "Client",
        "method": "__init__",
        "max_constructor_decisions": 2,
        "count": 3,
        "decisions": [
            {"kind": "For", "line": 3, "column": 9},
            {"kind": "If", "line": 4, "column": 13},
            {"kind": "Raise", "line": 5, "column": 17},
        ],
        "automatic_rewrite": False,
    }
    assert (root / "app/model.py").read_text() == SOURCE


@pytest.mark.parametrize("method", ["__init__", "__post_init__"])
def test_reviews_both_constructor_methods(tmp_path: Path, method: str) -> None:
    [finding] = violations(
        _project(tmp_path, SOURCE.replace("__init__", method)), SELECT
    )
    assert finding.expected is not None
    assert finding.expected["method"] == method


@pytest.mark.parametrize(
    "body",
    [
        "self.value = value if value is not None else default()",
        "if value is None:\n            raise ValueError('required')",
        "self.value = validate(value)",
        "def normalize():\n            if value:\n                for x in value:\n                    raise ValueError(x)\n        self.value = normalize()",
        "normalize = lambda: a if value else b if ready else c\n        self.value = normalize()",
        "class Nested:\n            if value:\n                for x in value:\n                    raise ValueError(x)\n        self.value = Nested()",
    ],
)
def test_accepts_fallback_guard_helper_and_nested_scopes(
    tmp_path: Path, body: str
) -> None:
    source = f"class Client:\n    def __init__(self, value):\n        {body}\n"
    assert violations(_project(tmp_path, source), SELECT) == []


@pytest.mark.parametrize(
    ("statement", "kinds"),
    [
        ("while ready:\n            break", ["While"]),
        (
            "try:\n            read()\n        except Exception:\n            recover()",
            ["Try"],
        ),
        (
            "try:\n            read()\n        except* ValueError:\n            recover()",
            ["TryStar"],
        ),
        ("match value:\n            case None:\n                pass", ["Match"]),
        ("self.value = [x for x in values]", ["ListComp"]),
        ("self.value = {x for x in values}", ["SetComp"]),
        ("self.value = {x: x for x in values}", ["DictComp"]),
        ("self.value = (x for x in values)", ["GeneratorExp"]),
        ("self.value = [x if ready else None for x in values]", ["ListComp", "IfExp"]),
        ("async for value in values:\n            consume(value)", ["AsyncFor"]),
    ],
)
def test_zero_limit_counts_each_documented_decision(
    tmp_path: Path, statement: str, kinds: list[str]
) -> None:
    method = "async def" if "async for" in statement else "def"
    source = (
        f"class Client:\n    {method} __init__(self, values):\n        {statement}\n"
    )
    root = _project(
        tmp_path, source, settings="style: {max_constructor_decisions: 0}\n"
    )
    [finding] = violations(root, SELECT)
    assert finding.expected is not None
    assert finding.expected["count"] == len(kinds)
    assert [node["kind"] for node in finding.expected["decisions"]] == kinds


def test_ignores_other_methods_free_functions_and_tests(tmp_path: Path) -> None:
    root = _project(tmp_path, SOURCE.replace("__init__", "__new__"))
    write_project(
        root,
        {
            "app/validators.py": SOURCE.replace("__init__", "validate"),
            "app/functions.py": "def __init__(value):\n    if value:\n        for x in value:\n            raise ValueError(x)\n",
            "tests/test_model.py": SOURCE,
        },
    )
    assert violations(root, SELECT) == []


def test_global_and_closest_package_limits_including_zero(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        SOURCE,
        settings="""\
rules: {complexity/excess-constructor-decisions: warning}
style:
  max_constructor_decisions: 3
  overrides:
    outer: {match: app, max_constructor_decisions: 0}
    inner: {match: app.legacy, max_constructor_decisions: 3}
""",
    )
    write_project(root, {"app/legacy/__init__.py": "", "app/legacy/model.py": SOURCE})
    [finding] = violations(root)
    assert (finding.path, finding.severity) == ("app/model.py", Severity.WARNING)
    assert finding.expected is not None
    assert finding.expected["max_constructor_decisions"] == 0
    (root / "smelt.yml").write_text(
        CONFIG
        + "rules: {complexity/excess-constructor-decisions: hint}\nstyle: {max_constructor_decisions: 3}\n",
        encoding="utf-8",
    )
    assert violations(root) == []
    assert violations(root, CheckOptions(use_cache=False)) == []


def test_opt_in_off_override_and_suppression(tmp_path: Path) -> None:
    root = _project(
        tmp_path, SOURCE, settings="style: {max_constructor_decisions: 0}\n"
    )
    assert [v.code for v in violations(root)] == ["SMT364"]
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {complexity/excess-constructor-decisions: warning}
style:
  overrides:
    quiet: {match: app.quiet, rules: {complexity/excess-constructor-decisions: off}}
""",
            "app/quiet/__init__.py": SOURCE,
            "app/intentional.py": SOURCE.replace(
                "def __init__(self, values):",
                "def __init__(self, values):  # smelt: ignore[SMT364] -- preserve ordered resource setup",
            ),
        },
    )
    [finding] = violations(root)
    assert finding.path == "app/model.py"
    assert violations(root, CheckOptions(use_cache=False)) == violations(root)


@pytest.mark.parametrize(
    "setting",
    [
        "style: {max_constructor_decisions: -1}",
        "style: {overrides: {bad: {match: app, max_constructor_decisions: -1}}}",
    ],
)
def test_rejects_negative_limits(tmp_path: Path, setting: str) -> None:
    with pytest.raises(ConfigError, match="max_constructor_decisions"):
        violations(_project(tmp_path, "", settings=setting))


def _project(tmp_path: Path, source: str, *, settings: str = "") -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + settings,
            "app/__init__.py": "",
            "app/model.py": source,
        },
    )
