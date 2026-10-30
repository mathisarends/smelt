from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.debt import Debt
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


def _project(
    tmp_path: Path, source: str, policy: str, path: str = "tests/domain/test_service.py"
) -> Path:
    section = "tests:\n" if policy.startswith("  ") else ""
    return write_project(
        tmp_path,
        {
            "smelt.yaml": f"version: 1\nproject: {{root_packages: [app]}}\n{section}{policy}",
            "app/__init__.py": "",
            path: source,
        },
    )


@pytest.mark.parametrize(
    "source",
    [
        "from unittest.mock import Mock as Double\ndef test_run(): Double()",
        "from unittest import mock\ndef test_run(): mock.AsyncMock()",
        "import unittest.mock\ndef test_run(): unittest.mock.create_autospec(Service)",
        "def test_run(mocker): mocker.spy(service, 'run')",
        "import pytest\n@pytest.fixture\ndef fixture(mocker): mocker.MagicMock()",
        "from unittest.mock import patch\n@patch('app.repo')\ndef test_run(repo): pass",
    ],
)
def test_domain_mock_policy_covers_construction_and_patching(
    tmp_path: Path, source: str
) -> None:
    root = _project(tmp_path, source, "  forbid_mocks_in: ['tests/**/domain/**']\n")
    [finding] = violations(root, CheckOptions(select=("SMT421",)))
    assert finding.path == "tests/domain/test_service.py"
    assert finding.expected is not None
    assert finding.expected["operation"]


def test_fixture_file_is_covered_but_integration_path_is_allowed(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "from unittest.mock import Mock\ndef fixture(): return Mock()",
        "  forbid_mocks_in: ['tests/**/domain/**']\n",
        "tests/domain/conftest.py",
    )
    write_project(
        root,
        {
            "tests/integration/test_http.py": "from unittest.mock import Mock\ndef test_run(): Mock()"
        },
    )
    assert len(violations(root, CheckOptions(select=("SMT421",)))) == 1


@pytest.mark.parametrize(
    ("expression", "flagged"),
    [
        ("patch('app.service')", True),
        ("patch(target='app.service')", True),
        ("patch.multiple('app.service', one=1)", True),
        ("patch.dict('os.environ', {})", True),
        ("patch.object(module, 'service')", False),
        ("patch.dict(mapping, {})", False),
        ("patch(target)", False),
        ("patch(f'app.{name}')", False),
        ("other.patch('app.service')", False),
    ],
)
def test_string_patch_policy_is_precise(
    tmp_path: Path, expression: str, flagged: bool
) -> None:
    root = _project(
        tmp_path,
        f"from unittest.mock import patch\ndef test_run():\n    {expression}\n",
        "rules: {string-patch-target: error}\n",
    )
    assert bool(violations(root, CheckOptions(select=("SMT422",)))) is flagged


def test_patch_count_includes_decorators_and_object_patching(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from unittest.mock import patch\n@patch('app.first')\ndef test_run(mock):\n    patch.object(module, 'second')\n",
        "  max_patches_per_test: 1\n",
    )
    [finding] = violations(root, CheckOptions(select=("SMT423",)))
    assert finding.expected is not None
    assert len(finding.expected["patches"]) == 2


def test_patch_limit_zero_and_quality_exclusion(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "def test_run(mocker): mocker.patch('app.repo')",
        "  max_patches_per_test: 0\n  quality_exclude: ['tests/domain/**']\n",
    )
    assert violations(root, CheckOptions(select=("SMT423",))) == []


def test_scope_and_inline_suppression_are_honored(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from unittest.mock import patch\ndef test_run():\n    patch('app.repo')  # smelt: ignore[SMT422] -- deliberately test lookup\n",
        "rules: {string-patch-target: error}\n",
    )
    write_project(
        root,
        {
            "tests/test_other.py": "from unittest.mock import patch\ndef test_run(): patch('app.repo')"
        },
    )
    outcome = check(root, CheckOptions(select=("SMT422",), paths=("tests/domain",)))
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1


def test_an_extra_patch_cannot_hide_behind_existing_debt(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "def test_run(mocker):\n    mocker.patch('app.one')\n    mocker.patch('app.two')\n",
        "  max_patches_per_test: 1\n",
    )
    options = CheckOptions(select=("SMT423",))
    initial = check(root, options)
    debt = Debt.from_violations(
        initial.report.violations, snippet_reader(initial.context)
    )
    source = root / "tests/domain/test_service.py"
    source.write_text(source.read_text() + "    mocker.patch('app.three')\n")
    after = check(root, options)
    new, known, _ = debt.match(after.report.violations, snippet_reader(after.context))
    assert len(new) == 1
    assert len(known) == 1
    assert known[0].expected is not None
    assert len(known[0].expected["patch_edges"]) == 2


def test_duplicate_patch_calls_keep_separate_baseline_tokens(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "def test_run(mocker):\n    mocker.patch('app.one')\n    mocker.patch('app.one')\n",
        "  max_patches_per_test: 0\n",
    )
    outcome = check(root, CheckOptions(select=("SMT423",)))
    debt = Debt.from_violations(
        outcome.report.violations, snippet_reader(outcome.context)
    )
    assert len({entry.fingerprint for entry in debt.entries}) == 2


@pytest.mark.parametrize(
    ("expression", "flagged"),
    [
        ("monkeypatch.setattr('app.service', replacement)", True),
        ("monkeypatch.setattr(target='app.service', value=replacement)", True),
        ("monkeypatch.setattr('app.service', value=replacement)", True),
        ("monkeypatch.setattr('app.service', replacement, raising=False)", True),
        ("monkeypatch.delattr('app.service', raising=False)", True),
        ("monkeypatch.setattr(module, 'service', replacement)", False),
        ("monkeypatch.setattr(module, name='service', value=replacement)", False),
        ("monkeypatch.setattr('app', 'service', replacement)", False),
        ("monkeypatch.delattr(module, 'service')", False),
        ("monkeypatch.setattr(target, replacement)", False),
        ("monkeypatch.setenv('APP_ENV', 'test')", False),
        ("monkeypatch.setitem(mapping, 'app.service', replacement)", False),
    ],
)
def test_monkeypatch_dotted_target_overloads(
    tmp_path: Path, expression: str, flagged: bool
) -> None:
    root = _project(
        tmp_path,
        f"def test_run(monkeypatch):\n    {expression}\n",
        "rules: {string-patch-target: error}\n",
    )
    found = violations(root, CheckOptions(select=("SMT422",)))
    assert bool(found) is flagged
    if found:
        assert found[0].expected is not None
        assert found[0].expected["family"] == "pytest.MonkeyPatch"
        assert found[0].expected["provenance"] == "fixture"


@pytest.mark.parametrize(
    "source",
    [
        "def test_run(monkeypatch):\n    replacement = monkeypatch\n    replacement.setattr('app.service', fake)",
        "import pytest\ndef test_run(mp: pytest.MonkeyPatch): mp.setattr('app.service', fake)",
        "from pytest import MonkeyPatch as MP\ndef test_run():\n    mp = MP()\n    mp.delattr('app.service')",
        "def test_run(monkeypatch):\n    with monkeypatch.context() as mp:\n        mp.setattr('app.service', fake)",
        "from pytest import MonkeyPatch\ndef test_run():\n    with MonkeyPatch.context() as mp:\n        mp.setattr('app.service', fake)",
    ],
)
def test_monkeypatch_aliases_types_and_context_are_counted(
    tmp_path: Path, source: str
) -> None:
    root = _project(
        tmp_path,
        source,
        "  forbid_mocks_in: ['tests/domain/**']\n  max_patches_per_test: 0\nrules: {string-patch-target: error}\n",
    )
    found = violations(root)
    assert {finding.code for finding in found} == {"SMT421", "SMT422", "SMT423"}
    assert all(finding.expected is not None for finding in found)


@pytest.mark.parametrize(
    "source",
    [
        "def helper(monkeypatch): monkeypatch.setattr('app.service', fake)",
        "def test_run(monkeypatch):\n    monkeypatch = unrelated\n    monkeypatch.setattr('app.service', fake)",
        "import pytest\n@pytest.fixture\ndef monkeypatch(): return unrelated\ndef test_run(monkeypatch): monkeypatch.setattr('app.service', fake)",
        "def test_run(monkeypatch):\n    monkeypatch.setenv('NAME', 'value')\n    monkeypatch.delenv('NAME')\n    monkeypatch.setitem(mapping, 'key', 'value')\n    monkeypatch.chdir(directory)",
    ],
)
def test_unrelated_monkeypatch_and_environment_setup_are_not_mocks(
    tmp_path: Path, source: str
) -> None:
    root = _project(
        tmp_path,
        source,
        "  forbid_mocks_in: ['tests/domain/**']\n  max_patches_per_test: 0\n",
    )
    assert violations(root) == []
