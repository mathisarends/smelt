from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "\nversion: 1\nproject:\n  root_packages:\n  - app\nstructure:\n  contracts:\n    definitions:\n      match: app\n      ordering: {}\n      rules:\n        public-definitions-first: error\n"
OPTIONS = CheckOptions(select=("SMT313",))


@pytest.mark.parametrize(
    ("source", "line", "expression"),
    [
        ("def _helper(): pass\ndef api():\n    return globals()\n", 3, "globals"),
        (
            "class C(Base):\n    def _helper(self): pass\n    def api(self): pass\n",
            1,
            "Base",
        ),
        (
            "@decorate\nclass C:\n    def _helper(self): pass\n    def api(self): pass\n",
            1,
            "decorate",
        ),
        (
            "from typing import TypeVar\ndef api(): pass\n_T = TypeVar(\n    '_T',\n    bound=Bound,\n)\n",
            5,
            "Bound",
        ),
    ],
)
def test_fix_refusal_identifies_the_trigger_without_changing_source(
    tmp_path: Path, source: str, line: int, expression: str
) -> None:
    write_project(tmp_path, {"smelt.yaml": CONFIG, "app/module.py": source})
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["fixable"] is False
    blocker = finding.expected["fix_blocker"]
    assert blocker["path"] == "app/module.py"
    assert blocker["line"] == line
    assert blocker["expression"] == expression
    assert f"app/module.py:{line}:" in finding.expected["fix_reason"]
    assert (tmp_path / "app/module.py").read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    ("minimum", "future", "count"),
    [(None, False, 0), ("3.13", False, 0), ("3.14", False, 1), ("3.13", True, 1)],
)
def test_declared_version_and_future_control_annotation_dependencies(
    tmp_path: Path, minimum: str | None, future: bool, count: int
) -> None:
    config = (
        CONFIG.replace(
            "  root_packages:\n  - app\n",
            f'  root_packages:\n  - app\n  python_version: "{minimum}"\n',
        )
        if minimum
        else CONFIG
    )
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/module.py": ("from __future__ import annotations\n" if future else "")
            + "_T = int\ndef api(value: _T): pass\n",
        },
    )

    found = violations(tmp_path, OPTIONS)

    assert len(found) == count
    if count:
        assert found[0].expected is not None
        assert found[0].expected["deferred_annotations"]


def test_check_exposes_execution_barriers_and_expected_order(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/module.py": "import sys\ndef _helper(): pass\ndef api(): pass\nregister()\n",
        },
    )

    outcome = check(tmp_path, OPTIONS)
    finding = outcome.report.violations[0]

    assert finding.code == "SMT313"
    assert finding.expected is not None
    assert finding.expected["original"] == ["_helper", "api"]
    assert finding.expected["order"] == ["api", "_helper"]
    assert finding.expected["fixable"]
    assert outcome.report.coverage is not None
    assert outcome.report.coverage["ordering"][0]["barriers"]


def test_inner_disabled_policy_overrides_parent_ordering(tmp_path: Path) -> None:
    config = (
        CONFIG
        + "    legacy:\n      match: app.legacy\n      ordering: {}\n      rules:\n        public-definitions-first: 'off'\n"
    )
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/legacy/__init__.py": "def _helper(): pass\ndef api(): pass\n",
            "app/module.py": "def _helper(): pass\ndef api(): pass\n",
        },
    )

    assert [v.path for v in violations(tmp_path, OPTIONS)] == ["app/module.py"]


def test_ordering_is_opt_in(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/module.py": "def _helper(): pass\ndef api(): pass\n",
        },
    )

    assert violations(tmp_path) == []
    assert [v.code for v in violations(tmp_path, OPTIONS)] == ["SMT313"]


@pytest.mark.parametrize("minimum", ["3.12", "3.13", "3.14"])
@pytest.mark.parametrize(
    "annotation", ["str | bytes", "str | bytes | None", "None | int"]
)
def test_builtin_union_annotations_allow_public_first(
    tmp_path: Path, minimum: str, annotation: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "project:\n", f'project:\n  python_version: "{minimum}"\n'
            ),
            "app/module.py": f"def _helper(value: {annotation}): return value\ndef api(value): return _helper(value)\n",
        },
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.code == "SMT313"
    assert finding.expected is not None
    assert finding.expected["order"] == ["api", "_helper"]
    assert finding.expected["fixable"]


@pytest.mark.parametrize("minimum", [None, "3.9"])
def test_builtin_unions_remain_unknown_without_a_supported_declared_target(
    tmp_path: Path, minimum: str | None
) -> None:
    config = (
        CONFIG
        if minimum is None
        else CONFIG.replace("project:\n", f'project:\n  python_version: "{minimum}"\n')
    )
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/module.py": "def _helper(value: str | bytes): pass\ndef api(): pass\n",
        },
    )
    outcome = check(tmp_path, OPTIONS)
    assert outcome.report.violations == []
    assert outcome.report.coverage is not None
    assert (
        "eager annotation"
        in outcome.report.coverage["ordering"][0]["barriers"][0]["reason"]
    )


@pytest.mark.parametrize(
    "source",
    [
        "from other import Text\ndef _helper(value: Text | bytes): pass\ndef api(): pass\n",
        "class Text: pass\ndef _helper(value: Text | bytes): pass\ndef api(): pass\n",
        "str = custom\ndef _helper(value: str | bytes): pass\ndef api(): pass\n",
        "def _helper(value=str | bytes): pass\ndef api(): pass\n",
        "def _helper(value: str | 1): pass\ndef api(): pass\n",
        "def _helper(value: None | None | int): pass\ndef api(): pass\n",
        "class Service:\n    str = custom\n    def _helper(self, value: str | bytes): pass\n    def api(self): pass\n",
        "def _replace():\n    global str\n    str = custom\n_replace()\ndef _helper(value: str | bytes): pass\ndef api(): pass\n",
        "import builtins as b\ndef _replace():\n    b.str = custom\n_replace()\ndef _helper(value: str | bytes): pass\ndef api(): pass\n",
    ],
)
def test_unknown_union_operands_shadowing_and_defaults_stay_pinned(
    tmp_path: Path, source: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "project:\n", 'project:\n  python_version: "3.12"\n'
            ),
            "app/module.py": source,
        },
    )
    outcome = check(tmp_path, OPTIONS)
    assert outcome.report.violations == []
    assert outcome.report.coverage is not None
    assert any(item["barriers"] for item in outcome.report.coverage["ordering"])


def test_builtin_import_aliases_and_method_return_unions_are_supported(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "project:\n", 'project:\n  python_version: "3.12"\n'
            ),
            "app/module.py": "from builtins import str as Text\nclass Service:\n    def _helper(self) -> Text | bytes: pass\n    def api(self): pass\n",
        },
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["order"] == ["api", "_helper"]


def test_public_first_retains_a_needed_future_import(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "project:\n", 'project:\n  python_version: "3.12"\n'
            ).replace(
                "      ordering: {}",
                "      ordering: {}\n      future_annotations: when-needed",
            ),
            "app/module.py": "from __future__ import annotations\nclass Keyboard:\n    def press(self, value: _Stroke): pass\nclass _Stroke: pass\n",
        },
    )
    assert violations(tmp_path, CheckOptions(select=("SMT311", "SMT313"))) == []
