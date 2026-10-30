from __future__ import annotations

import subprocess
import sys
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
            "smelt.yaml": f"version: 1\nproject: {{root_packages: [app]}}\nrules: {{async-mock-without-await-check: {'warning' if enabled else 'off'}}}",
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
    found = violations(tmp_path, CheckOptions(select=("SMT428",)))
    assert len(found) == int(enabled)
    if enabled:
        [finding] = found
        assert finding.code == "SMT428"
        assert finding.line == 2
        assert finding.severity.value == "warning"
        assert finding.expected == {
            "test": "test_connect",
            "receiver": "accounts.connect",
            "async_callable": "app.Accounts.connect",
            "provenance": "fixture-return",
        }


def test_async_review_respects_exclusion_and_rule_off(tmp_path: Path) -> None:
    config = "version: 1\nproject:\n  root_packages:\n  - app\ntests:\n  quality_exclude:\n  - tests/excluded/**\nrules:\n  async-mock-without-await-check: 'off'\n"
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "tests/test_connect.py": "from unittest.mock import AsyncMock\ndef test_connect():\n    connect = AsyncMock()\n    connect.assert_called_once()",
            "tests/excluded/test_connect.py": "from unittest.mock import AsyncMock\ndef test_connect():\n    connect = AsyncMock()\n    connect.assert_called_once()",
        },
    )
    assert violations(tmp_path, CheckOptions(select=("SMT428",))) == []
    (tmp_path / "smelt.yaml").write_text(
        config.replace(
            "async-mock-without-await-check: 'off'",
            "async-mock-without-await-check: warning",
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
            "smelt.yaml": "version: 1\nproject:\n  root_packages:\n  - app\nrules:\n  async-mock-without-await-check: warning\n",
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
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject:\n  root_packages:\n  - app\nrules:\n  async-mock-without-await-check: warning\n",
            "app/__init__.py": "",
            "app/accounts.py": "class Accounts:\n    async def connect(self, key): pass",
            "app/handler.py": "async def handle(accounts):\n    "
            + ("" if missing_await else "await ")
            + "accounts.connect('id')\n    return {'connected': True}",
            "tests/test_handler.py": "from unittest.mock import AsyncMock\nfrom app.accounts import Accounts\nfrom app.handler import handle\nasync def test_connect():\n    accounts = AsyncMock(spec=Accounts)\n    assert await handle(accounts) == {'connected': True}\n    accounts.connect."
            + assertion
            + "('id')",
        },
    )
    assert [finding.code for finding in violations(tmp_path)] == (
        [] if check_await else ["SMT428"]
    )
    # Capture the mutation's warning as evidence without changing suite policy.
    executed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio, gc, runpy, warnings\n"
            "test = runpy.run_path('tests/test_handler.py')['test_connect']\n"
            "with warnings.catch_warnings(record=True) as observed:\n"
            "    warnings.simplefilter('always')\n"
            "    try:\n"
            "        asyncio.run(test())\n"
            "    except AssertionError:\n"
            "        print('assertion-failed')\n"
            "        raise SystemExit(1)\n"
            "    gc.collect()\n"
            "    print('unawaited-warning' if any(issubclass(w.category, RuntimeWarning) for w in observed) else 'no-warning')\n",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert executed.returncode == int(missing_await and check_await), (
        executed.stdout + executed.stderr
    )
    assert executed.stdout.strip() == (
        "assertion-failed"
        if missing_await and check_await
        else "unawaited-warning"
        if missing_await
        else "no-warning"
    )
