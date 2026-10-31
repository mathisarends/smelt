from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "\nversion: 1\nproject:\n  root_packages:\n  - app\nrules:\n  test-only-public-function: warning\n"


def test_excluded_script_callers_are_explicitly_unassessed(tmp_path: Path) -> None:
    config = CONFIG.replace(
        "  root_packages:", "  exclude: ['scripts/**']\n  root_packages:"
    )
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "app/service.py": "def helper(): return 1\n",
            "tests/test_service.py": "from app.service import helper\ndef test_run(): assert helper() == 1\n",
            "scripts/generate.py": "from app.service import helper\nhelper()\n",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.expected is not None
    scope = finding.expected["usage_scope"]
    assert scope["excluded_paths"] == ["scripts/**"]
    assert "among analyzed modules" in finding.message
    assert finding.hint is not None
    assert "maintainer scripts" in finding.hint
    assert "not assessed" in finding.hint
    assert (
        (tmp_path / "scripts/generate.py")
        .read_text(encoding="utf-8")
        .startswith("from app.service")
    )


def test_async_gesture_helper_used_locally_and_only_exposed_to_tests(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": """
                async def drag_gesture(tab):
                    await tab.move()

                async def drag(tab):
                    await drag_gesture(tab)
                    await drag_gesture(tab)
            """,
            "tests/test_mouse.py": "from app.mouse import drag_gesture\nasync def test_drag(tab): await drag_gesture(tab)",
        },
    )
    [finding] = violations(tmp_path)
    assert (finding.code, finding.path, finding.line) == (
        "SMT429",
        "app/mouse.py",
        1,
    )
    assert finding.severity == Severity.WARNING
    assert finding.source_module == "app.mouse"
    assert finding.expected == {
        "function": "drag_gesture",
        "suggested_name": "_drag_gesture",
        "test_references": [{"path": "tests/test_mouse.py", "line": 1}],
        "usage_scope": {
            "root_packages": ["app"],
            "source_roots": ["."],
            "test_roots": ["tests"],
            "excluded_paths": [],
            "unassessed_consumers": [
                "excluded files (including maintainer scripts)",
                "files outside selected packages and roots",
                "external consumers and arbitrary dynamic access",
            ],
        },
    }
    assert finding.hint is not None
    assert "_drag_gesture" in finding.hint


@pytest.mark.parametrize(
    "test_source",
    [
        "from app.mouse import helper as gesture\ndef test_drag(): gesture()",
        "import app.mouse as mouse\ndef test_drag(): mouse.helper()",
        "import app.mouse\ndef test_drag(): app.mouse.helper()",
        "from app import mouse as m\ndef test_drag(): m.helper()",
        "def test_drag():\n    from app.mouse import helper\n    helper()",
    ],
)
def test_direct_and_module_aliased_test_access(
    tmp_path: Path, test_source: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": "def helper(): pass",
            "tests/test_mouse.py": test_source,
        },
    )
    [finding] = violations(tmp_path)
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_helper"


@pytest.mark.parametrize(
    "production_source",
    [
        "from app.mouse import helper",
        "from .mouse import helper as gesture\ndef run(): gesture()",
        "import app.mouse as m\ndef run(): m.helper()",
        "import app.mouse\ndef run(): app.mouse.helper()",
        "from app import mouse\ndef run(): mouse.helper()",
        "from app.mouse import helper as public_api",
        "from app.mouse import *",
        "from app import mouse\nalias = mouse\ndef run(): alias.helper()",
        "import app.mouse as m\ndef run(name): return getattr(m, name)",
        "import app as package\ndef run(): return consume(package)",
        "import app.mouse as m\ndef run(): m.helper()\ndef unrelated():\n    import other as m\n    m.run()",
    ],
)
def test_production_consumers_exports_and_escaped_modules_preserve_visibility(
    tmp_path: Path, production_source: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": "def helper(): pass",
            "app/consumer.py": production_source,
            "tests/test_mouse.py": "from app.mouse import helper",
        },
    )
    assert violations(tmp_path) == []


@pytest.mark.parametrize(
    "production_source",
    [
        "from app import mouse\ndef run(): mouse.drag()",
        "import app.mouse as m\ndef run(): m.drag()",
        "import app.mouse\ndef run(): app.mouse.drag()",
    ],
)
def test_using_public_caller_does_not_make_its_helper_public(
    tmp_path: Path, production_source: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": "def helper(): pass\ndef drag(): helper()",
            "app/consumer.py": production_source,
            "tests/test_mouse.py": "from app.mouse import helper",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.expected is not None
    assert finding.expected["function"] == "helper"


@pytest.mark.parametrize(
    "source",
    [
        "__all__ = ['helper']\ndef helper(): pass",
        "__all__: tuple[str, ...] = ('helper',)\ndef helper(): pass",
        "__all__ = []\n__all__ += ['helper']\ndef helper(): pass",
        "__all__ = make_exports()\ndef helper(): pass",
        "__all__ = []\n__all__.append('helper')\ndef helper(): pass",
        "@register\ndef helper(): pass",
        "class Service:\n    def helper(self): pass",
        "def outer():\n    def helper(): pass\n    return helper",
        "def helper(): pass\nhelper = replacement",
        "def helper(): pass\nif condition:\n    def helper(): pass",
    ],
)
def test_explicit_apis_and_uncertain_definitions_are_excluded(
    tmp_path: Path, source: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": source,
            "tests/test_mouse.py": "from app.mouse import helper",
        },
    )
    assert violations(tmp_path) == []


def test_exports_of_other_functions_do_not_hide_internal_helpers(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": "__all__ = ['drag']\ndef helper(): pass\ndef drag(): helper()",
            "tests/test_mouse.py": "from app.mouse import helper",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.line == 2


@pytest.mark.parametrize("name", ["_helper", "__helper__"])
def test_private_and_dunder_functions_are_excluded(tmp_path: Path, name: str) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": f"def {name}(): pass",
            "tests/test_mouse.py": f"from app.mouse import {name}",
        },
    )
    assert violations(tmp_path) == []


@pytest.mark.parametrize(
    "test_source",
    [
        "def helper(): pass\ndef test_drag(): helper()",
        "from app import mouse\ndef test_drag(mouse): mouse.helper()",
        "import app.mouse as m\ndef test_drag():\n    m = fake\n    m.helper()",
        "def test_drag(): pass",
    ],
)
def test_local_fakes_shadowed_receivers_and_untested_functions_are_excluded(
    tmp_path: Path, test_source: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": "def helper(): pass",
            "tests/test_mouse.py": test_source,
        },
    )
    assert violations(tmp_path) == []


def test_facade_reexports_remain_public(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "from .api import gesture as gesture",
            "app/api.py": "from .mouse import helper as gesture",
            "app/mouse.py": "def helper(): pass",
            "tests/test_mouse.py": "from app import gesture\ndef test_drag(): gesture()",
        },
    )
    assert violations(tmp_path) == []


def test_renaming_helper_and_testing_public_behavior_resolves_the_finding(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": "__all__ = ['drag']\ndef helper(): return 'moved'\ndef drag(): return helper()",
            "tests/test_mouse.py": "from app.mouse import helper\ndef test_drag(): assert helper() == 'moved'",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_helper"

    write_project(
        tmp_path,
        {
            "app/mouse.py": "__all__ = ['drag']\ndef _helper(): return 'moved'\ndef drag(): return _helper()",
            "tests/test_mouse.py": "from app.mouse import drag\ndef test_drag(): assert drag() == 'moved'",
        },
    )
    assert violations(tmp_path) == []


def test_excluded_production_is_not_mistaken_for_evidence_of_public_use(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject:\n  root_packages:\n  - app\n  exclude:\n  - app/generated/**\nrules:\n  test-only-public-function: warning\n",
            "app/__init__.py": "",
            "app/mouse.py": "def helper(): pass",
            "app/generated/consumer.py": "from app.mouse import helper",
            "tests/test_mouse.py": "from app.mouse import helper",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.code == "SMT429"


@pytest.mark.parametrize(
    ("policy", "options"),
    [
        ("", CheckOptions()),
        (
            "rules:\n  test-only-public-function: 'off'\n",
            CheckOptions(select=("ALL",)),
        ),
        (
            "rules: {SMT429: off}\n",
            CheckOptions(),
        ),
        (
            "rules:\n  test-only-public-function: warning\n",
            CheckOptions(ignore=("SMT429",)),
        ),
        (
            "tests:\n  quality_exclude:\n  - tests/**\nrules:\n  test-only-public-function: warning\n",
            CheckOptions(),
        ),
    ],
)
def test_rule_selection_and_test_evidence_exclusions(
    tmp_path: Path, policy: str, options: CheckOptions
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": f"version: 1\nproject: {{root_packages: [app]}}\n{policy}",
            "app/__init__.py": "",
            "app/mouse.py": "def helper(): pass",
            "tests/test_mouse.py": "from app.mouse import helper",
        },
    )
    assert not any(
        finding.code == "SMT429" for finding in violations(tmp_path, options)
    )


def test_custom_roots_and_deterministic_evidence(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject:\n  root_packages:\n  - app\n  source_roots:\n  - src\n  test_roots:\n  - spec\nrules:\n  test-only-public-function: warning\n",
            "src/app/__init__.py": "",
            "src/app/mouse.py": "def helper(): pass",
            "spec/test_b.py": "from app.mouse import helper",
            "spec/test_a.py": "from app.mouse import helper as h\ndef test_drag(): h()",
        },
    )
    outcome = check(tmp_path)
    [finding] = outcome.report.violations
    assert finding.path == "src/app/mouse.py"
    assert finding.expected is not None
    assert finding.expected["test_references"] == [
        {"path": "spec/test_a.py", "line": 1},
        {"path": "spec/test_b.py", "line": 1},
    ]


def test_shared_private_name_suggestion_avoids_an_existing_function(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/mouse.py": "def _helper(): pass\ndef helper(): pass",
            "tests/test_mouse.py": "from app.mouse import helper",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_helper_internal"
