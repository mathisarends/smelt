from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        ("run()", True),
        ("assert True", True),
        ("mock.save.assert_called_once()", True),
        ("assert result.status == 'active'", False),
        ("assert result", False),
        ("with pytest.raises(ValueError):\n        run()", False),
        ("with pytest.warns(UserWarning):\n        run()", False),
        ("def helper():\n        assert result == 1", True),
        ("def helper():\n        with pytest.raises(ValueError): run()", True),
        ("manager = lambda: pytest.raises(ValueError)", True),
    ],
)
def test_outcome_evidence(tmp_path: Path, body: str, flagged: bool) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {require_assertions: true}",
            "app/__init__.py": "",
            "tests/test_service.py": "import pytest\ndef test_run():\n    "
            + body
            + "\n",
        },
    )
    found = violations(tmp_path, CheckOptions(select=("SMT424",)))
    assert bool(found) is flagged
    if found:
        assert found[0].severity.value == "warning"
        assert found[0].line == 2


def test_assertion_helper_is_configurable_and_alias_aware(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {require_assertions: true, assertion_helpers: [tests.assertions.assert_user]}",
            "app/__init__.py": "",
            "tests/test_service.py": "from tests.assertions import assert_user as verify\ndef test_run(): verify(user)",
        },
    )
    assert violations(tmp_path, CheckOptions(select=("SMT424",))) == []


def test_default_configuration_keeps_quality_rules_opt_in(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}",
            "app/__init__.py": "",
            "tests/test_service.py": "from app import _secret\nfrom unittest.mock import Mock, patch\ndef test_run():\n    patch('app.repo')\n    Mock()\n",
        },
    )
    assert violations(tmp_path, CheckOptions(select=("SMT42",))) == []


def test_vacuity_review_is_separate_from_missing_outcomes_and_honors_exclusion(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {require_assertions: true, require_non_vacuous_assertions: true, quality_exclude: ['tests/excluded/**']}",
            "app/__init__.py": "",
            "tests/test_codegen.py": "def test_codegen():\n    for name, content in generate().items():\n        assert committed[name] == content",
            "tests/excluded/test_codegen.py": "def test_codegen():\n    assert all(generate())",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.code == "SMT425"
    assert finding.severity.value == "warning"
    assert finding.expected is not None
    assert finding.expected["evidence"] == [{"line": 2, "kind": "loop"}]


def test_typed_conftest_mock_fixture_can_express_an_adapter_contract(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {require_assertions: true}",
            "app/__init__.py": "",
            "tests/conftest.py": "import pytest\nfrom unittest.mock import AsyncMock\n@pytest.fixture\ndef transport() -> AsyncMock: return AsyncMock()",
            "tests/test_adapter.py": "async def test_adapter(transport):\n    await navigate(transport)\n    transport.assert_awaited_once_with('Page.navigate', url='https://example.test')",
        },
    )
    assert violations(tmp_path) == []
