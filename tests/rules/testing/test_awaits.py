from __future__ import annotations

import asyncio
import gc
import runpy
import warnings
from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("enabled", [False, True])
def test_async_review_uses_class_specs_from_a_conftest_fixture_and_facade(
    tmp_path: Path, enabled: bool
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": f"version: 2\nproject: {{root_packages: [app]}}\nrules: {{tests/missing-await-assertion: {'warning' if enabled else 'off'}}}",
            "app/__init__.py": "from app.accounts import Accounts",
            "app/accounts.py": "class Accounts:\n    async def connect(self, key): pass\n    def terminate(self): pass",
            "tests/conftest.py": "import pytest\nfrom unittest.mock import AsyncMock\nfrom app import Accounts\n@pytest.fixture\ndef accounts() -> AsyncMock:\n    return AsyncMock(spec=Accounts)",
            "tests/test_accounts.py": """
                def test_connect(accounts):
                    args, kwargs = accounts.connect.call_args
                    assert args == ('id',)
                def test_terminate(accounts):
                    accounts.terminate.assert_called_once()
                def test_awaited(accounts):
                    accounts.connect.assert_awaited_once_with('id')
                def test_scheduled(accounts):
                    accounts.connect.assert_called_once()
                    accounts.connect.assert_not_awaited()
            """,
        },
    )
    found = violations(tmp_path, CheckOptions(select=("TST010",)))
    assert len(found) == int(enabled)
    if enabled:
        [finding] = found
        assert finding.code == "TST010"
        assert finding.line == 2
        assert finding.severity.value == "warning"
        assert finding.expected == {
            "test": "test_connect",
            "receiver": "accounts.connect",
            "async_callable": "app.Accounts.connect",
            "provenance": "fixture-return",
        }


def test_async_review_respects_exclusion_and_rule_off(tmp_path: Path) -> None:
    config = "version: 2\nproject:\n  root_packages:\n  - app\ntests:\n  quality_exclude:\n  - tests/excluded/**\nrules:\n  tests/missing-await-assertion: 'off'\n"
    write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "tests/test_connect.py": "from unittest.mock import AsyncMock\ndef test_connect():\n    connect = AsyncMock()\n    connect.assert_called_once()",
            "tests/excluded/test_connect.py": "from unittest.mock import AsyncMock\ndef test_connect():\n    connect = AsyncMock()\n    connect.assert_called_once()",
        },
    )
    assert violations(tmp_path, CheckOptions(select=("TST010",))) == []
    (tmp_path / "smelt.yml").write_text(
        config.replace(
            "tests/missing-await-assertion: 'off'",
            "tests/missing-await-assertion: warning",
        )
    )
    [finding] = violations(tmp_path)
    assert finding.path == "tests/test_connect.py"


def test_async_spec_declared_in_conftest_is_available_to_its_consumer(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject:\n  root_packages:\n  - app\nrules:\n  tests/missing-await-assertion: warning\n",
            "app/__init__.py": "",
            "tests/conftest.py": "import pytest\nfrom unittest.mock import AsyncMock\nclass Accounts:\n    async def connect(self): pass\n@pytest.fixture\ndef accounts():\n    yield AsyncMock(spec=Accounts)",
            "tests/test_accounts.py": "def test_connect(accounts):\n    accounts.connect.assert_called_once()",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.expected is not None
    assert finding.expected["async_callable"] == "tests.conftest.Accounts.connect"


@pytest.mark.parametrize(
    "missing_await", [False, True], ids=["correct", "missing-await"]
)
@pytest.mark.parametrize(
    "check_await", [False, True], ids=["call-check", "await-check"]
)
def test_await_assertion_rejects_the_missing_await_mutation(
    tmp_path: Path, missing_await: bool, check_await: bool
) -> None:
    assertion = "assert_awaited_once_with" if check_await else "assert_called_once_with"
    await_prefix = "" if missing_await else "await "
    test_path = "tests/test_handler.py"
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject:\n  root_packages:\n  - app\nrules:\n  tests/missing-await-assertion: warning\n",
            "app/__init__.py": "",
            test_path: f"""
                from unittest.mock import AsyncMock

                class Accounts:
                    async def connect(self, key): pass

                async def handle(accounts):
                    {await_prefix}accounts.connect('id')
                    return {{'connected': True}}

                async def test_connect():
                    accounts = AsyncMock(spec=Accounts)
                    assert await handle(accounts) == {{'connected': True}}
                    accounts.connect.{assertion}('id')
            """,
        },
    )
    assert [finding.code for finding in violations(tmp_path)] == (
        [] if check_await else ["TST010"]
    )
    test_connect = runpy.run_path(str(tmp_path / test_path))["test_connect"]
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always", RuntimeWarning)
        if missing_await and check_await:
            with pytest.raises(AssertionError, match="to have been awaited once"):
                asyncio.run(test_connect())
        else:
            asyncio.run(test_connect())
        gc.collect()

    assert (
        any(issubclass(warning.category, RuntimeWarning) for warning in observed)
        is missing_await
    )
