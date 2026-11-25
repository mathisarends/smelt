from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config.errors import ConfigError
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("TST013",))


def _tests(root: Path, source: str, *, path: str = "tests/test_sync.py") -> None:
    write_project(root, {"smelt.yml": CONFIG, "app/__init__.py": "", path: source})


@pytest.mark.parametrize(
    ("header", "marker", "form", "kind"),
    [
        ("import pytest", "@pytest.mark.skip", "pytest.mark.skip", "skip"),
        ("import pytest", "@pytest.mark.skip()", "pytest.mark.skip", "skip"),
        (
            "import pytest",
            '@pytest.mark.skip(reason="broken")',
            "pytest.mark.skip",
            "skip",
        ),
        ("import pytest", '@pytest.mark.skip("broken")', "pytest.mark.skip", "skip"),
        (
            "import pytest",
            '@pytest.mark.skipif(True, reason="broken")',
            "pytest.mark.skipif",
            "skip",
        ),
        ("import pytest", "@pytest.mark.xfail", "pytest.mark.xfail", "xfail"),
        (
            "import pytest",
            '@pytest.mark.xfail(reason="flaky")',
            "pytest.mark.xfail",
            "xfail",
        ),
        (
            "import pytest",
            "@pytest.mark.flaky(reruns=3)",
            "pytest.mark.flaky",
            "rerun",
        ),
        ("from flaky import flaky", "@flaky(max_runs=3)", "flaky.flaky", "rerun"),
        ("from pytest import mark", "@mark.skip", "pytest.mark.skip", "skip"),
        ("import pytest as pt", "@pt.mark.xfail", "pytest.mark.xfail", "xfail"),
        ("import unittest", '@unittest.skip("later")', "unittest.skip", "skip"),
        (
            "import unittest",
            "@unittest.expectedFailure",
            "unittest.expectedFailure",
            "xfail",
        ),
    ],
)
def test_decorators_without_a_tracked_reason_are_reported(
    tmp_path: Path, header: str, marker: str, form: str, kind: str
) -> None:
    _tests(tmp_path, f"{header}\n\n{marker}\ndef test_sync():\n    assert True\n")
    [finding] = violations(tmp_path, OPTIONS)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "TST013",
        "tests/test_sync.py",
        3,
        2,
    )
    assert finding.expected is not None
    assert (finding.expected["form"], finding.expected["kind"]) == (form, kind)
    assert finding.expected["environment_condition"] is False
    assert "tracked reason" in finding.message


@pytest.mark.parametrize(
    "body",
    [
        "    pytest.skip()\n",
        '    pytest.skip("later")\n',
        '    pytest.xfail(reason="later")\n',
    ],
)
def test_calls_in_a_test_body_are_reported(tmp_path: Path, body: str) -> None:
    _tests(tmp_path, f"import pytest\n\ndef test_sync():\n{body}")
    [finding] = violations(tmp_path, OPTIONS)
    assert (finding.line, finding.column) == (4, 5)


def test_module_class_param_and_conftest_markers_are_reported(tmp_path: Path) -> None:
    _tests(
        tmp_path,
        "import pytest\n\n"
        "pytestmark = pytest.mark.skip\n\n"
        "@pytest.mark.parametrize('value', [pytest.param(1, marks=pytest.mark.xfail)])\n"
        "def test_value(value):\n    assert value\n",
    )
    write_project(
        tmp_path,
        {
            "tests/conftest.py": "import pytest\n\n"
            "def pytest_collection_modifyitems(items):\n"
            "    for item in items:\n"
            "        item.add_marker(pytest.mark.skip(reason='slow'))\n",
        },
    )
    found = violations(tmp_path, OPTIONS)
    assert [(v.path, v.line) for v in found] == [
        ("tests/conftest.py", 5),
        ("tests/test_sync.py", 3),
        ("tests/test_sync.py", 5),
    ]


@pytest.mark.parametrize(
    "source",
    [
        '@pytest.mark.skip(reason="#123: the sync API returns stale pages")\ndef test_sync(): ...\n',
        '@pytest.mark.xfail(reason="https://github.com/org/repo/issues/9")\ndef test_sync(): ...\n',
        "@pytest.mark.skip  # quarantine: #123 stale pages\ndef test_sync(): ...\n",
        "# Quarantined until #123 is fixed.\n@pytest.mark.flaky(reruns=3)\ndef test_sync(): ...\n",
        "def test_sync():\n    pytest.skip('#123: stale pages')\n",
        "@pytest.mark.skip(reason=REASON)\ndef test_sync(): ...\n",
    ],
)
def test_tracked_and_dynamic_reasons_are_accepted(tmp_path: Path, source: str) -> None:
    _tests(tmp_path, f"import pytest\n\nREASON = '#1'\n\n{source}")
    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(
    "source",
    [
        'import sys\nimport pytest\n\n@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals")\ndef test_signals(): ...\n',
        'import pytest\n\nHAS_NUMPY = False\n\n@pytest.mark.skipif(not HAS_NUMPY, reason="needs numpy")\ndef test_array(): ...\n',
        'import pytest\n\n@pytest.mark.xfail(strict=True, reason="rounds half to even")\ndef test_round(): ...\n',
        'import sys\nimport pytest\n\ndef test_signals():\n    if sys.platform == "win32":\n        pytest.skip("needs POSIX signals")\n',
        'import pytest\n\ntry:\n    import numpy\nexcept ImportError:\n    pytest.skip("needs numpy", allow_module_level=True)\n',
        'import unittest\nimport sys\n\n@unittest.skipIf(sys.platform == "win32", "needs POSIX signals")\ndef test_signals(): ...\n',
        'import pytest\n\nnumpy = pytest.importorskip("numpy")\n',
        "def test_skip_name(skip):\n    assert skip\n",
    ],
)
def test_environment_limits_with_a_reason_are_accepted(
    tmp_path: Path, source: str
) -> None:
    _tests(tmp_path, source)
    assert violations(tmp_path, OPTIONS) == []


def test_environment_limit_without_a_reason_is_reported(tmp_path: Path) -> None:
    _tests(
        tmp_path,
        "import sys\nimport pytest\n\n"
        '@pytest.mark.skipif(sys.platform == "win32")\ndef test_signals(): ...\n',
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["environment_condition"] is True
    assert finding.message == "pytest.mark.skipif skips a test without a reason"


def test_skip_reason_pattern_is_configurable(tmp_path: Path) -> None:
    _tests(
        tmp_path,
        'import pytest\n\n@pytest.mark.skip(reason="PROJ-12: stale pages")\n'
        "def test_sync(): ...\n",
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["skip_reason_pattern"] == r"#\d+|https?://\S+"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG + "tests: {skip_reason_pattern: 'PROJ-\\d+'}\n"},
    )
    assert violations(tmp_path, OPTIONS) == []


def test_invalid_skip_reason_pattern_is_a_configuration_error(tmp_path: Path) -> None:
    _tests(tmp_path, "def test_sync(): ...\n")
    write_project(
        tmp_path, {"smelt.yml": CONFIG + "tests: {skip_reason_pattern: '(#'}\n"}
    )
    with pytest.raises(ConfigError, match="skip_reason_pattern"):
        check(tmp_path, OPTIONS)


def test_opt_in_production_code_and_quality_exclusions(tmp_path: Path) -> None:
    source = "import pytest\n\n@pytest.mark.skip\ndef test_sync(): ...\n"
    _tests(tmp_path, source)
    write_project(tmp_path, {"app/checks.py": source})
    assert violations(tmp_path) == []
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG + "rules: {tests/missing-skip-reason: warning}\n"},
    )
    assert [v.path for v in violations(tmp_path)] == ["tests/test_sync.py"]
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + "tests: {quality_exclude: [tests/test_sync.py]}\n"
            "rules: {tests/missing-skip-reason: warning}\n"
        },
    )
    assert violations(tmp_path) == []
