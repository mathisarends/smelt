import ast

import pytest

from smelt.analysis.test_syntax import TestModule as SyntaxModule
from smelt.analysis.test_syntax import (
    _analyze_test_syntax,  # smelt: ignore[SMT420] -- Unit-test this parser boundary.
)


def _analyze(source: str) -> SyntaxModule:
    return _analyze_test_syntax(ast.parse(source), "tests/test_service.py")


@pytest.mark.parametrize(
    "source",
    [
        "from unittest.mock import patch as replace\nreplace('app.name')",
        "import unittest.mock as mock\nmock.patch('app.name')",
        "import unittest.mock\nunittest.mock.patch('app.name')",
        "def test_run(mocker):\n    mocker.patch('app.name')",
        "async def test_run(session_mocker):\n    session_mocker.patch('app.name')",
    ],
)
def test_known_patch_aliases(source: str) -> None:
    result = _analyze(source)
    assert len([call for call in result.calls if call.patch]) == 1
    assert next(call for call in result.calls if call.patch).string_target


@pytest.mark.parametrize(
    "body",
    [
        "def test_run(patch):\n    patch('x')",
        "def test_run():\n    patch('x')\n    patch = other",
        "def test_run():\n    patch = other\n    patch('x')",
        "def test_run():\n    for patch in values:\n        patch('x')",
        "def test_run():\n    with manager() as patch:\n        patch('x')",
        "def test_run():\n    try:\n        run()\n    except Error as patch:\n        patch('x')",
        "def test_run():\n    [patch('x') for patch in values]",
        "def test_run():\n    (lambda patch: patch('x'))(other)",
        "patch = other\npatch('x')",
        "if enabled:\n    patch = other\npatch('x')",
        "from somewhere import *\npatch('x')",
        "def test_run():\n    match value:\n        case patch:\n            patch('x')",
        "def patch(value): pass\npatch('x')",
    ],
)
def test_shadowed_patch_is_not_a_mock_operation(body: str) -> None:
    result = _analyze("from unittest.mock import patch\n" + body)
    assert not any(call.mock for call in result.calls)


def test_decorators_belong_to_the_test_but_nested_helpers_do_not() -> None:
    result = _analyze("""
from unittest.mock import patch
@patch('app.one')
def test_run(mock):
    patch.object(module, 'two')
    def helper():
        patch('app.three')
        assert result == 3
""")
    [test] = result.tests
    assert len([call for call in test.calls if call.patch]) == 2
    assert len([call for call in result.calls if call.patch]) == 3
    assert not test.has_assertion([])


def test_class_attributes_are_not_method_globals() -> None:
    result = _analyze("""
from unittest.mock import patch
class TestService:
    patch = other
    def test_run(self):
        patch('app.one')
""")
    assert len([call for call in result.calls if call.patch]) == 1


@pytest.mark.parametrize(
    ("source", "count"),
    [
        ("def test_run(): pass", 1),
        ("class TestService:\n    async def test_run(self): pass", 1),
        ("class Helper:\n    def test_run(self): pass", 0),
        ("def helper():\n    def test_nested(): pass", 0),
        (
            "from unittest import TestCase\nclass ServiceChecks(TestCase):\n    def test_run(self): self.assertEqual(1, 1)",
            1,
        ),
    ],
)
def test_test_collection_scope(source: str, count: int) -> None:
    assert len(_analyze(source).tests) == count


def test_unittest_assertions_and_aliased_helpers_are_evidence() -> None:
    result = _analyze("""
from unittest import TestCase
from tests.assertions import assert_user as verify
class ServiceChecks(TestCase):
    def test_run(self):
        self.assertEqual(result, 42)
def test_user():
    verify(result)
""")
    assert result.tests[0].has_assertion([])
    assert not result.tests[1].has_assertion([])
    assert result.tests[1].has_assertion(["tests.assertions.assert_user"])


def test_plain_helper_argument_is_not_inferred_to_be_a_pytest_fixture() -> None:
    result = _analyze("def helper(mocker): mocker.patch('app.name')")
    assert not any(call.patch for call in result.calls)


def test_generator_body_does_not_supply_an_assertion_to_its_owner() -> None:
    result = _analyze(
        "import pytest\ndef test_run():\n    checks = (pytest.raises(ValueError) for value in values)"
    )
    assert not result.tests[0].has_assertion([])


@pytest.mark.parametrize(
    ("body", "recognized"),
    [
        ("client = AsyncMock()\n    client.assert_awaited_once_with(42)", True),
        (
            "client = Mock()\n    alias = client\n    alias.save.assert_called_once()",
            True,
        ),
        ("client = Mock()\n    client.assert_something()", False),
        (
            "client = Mock()\n    client = unknown()\n    client.assert_called_once()",
            False,
        ),
        (
            "client = Mock()\n    if condition:\n        client = unknown()\n    client.assert_called_once()",
            False,
        ),
        ("other.assert_called_once()", False),
        (
            "with patch('app.client') as client:\n        client.assert_called_once()",
            True,
        ),
        ("client = Mock\n    client.assert_called_once()", False),
        ("client = patch('app.client')\n    client.assert_called_once()", False),
        (
            "with patch('app.client', replacement) as client:\n        client.assert_called_once()",
            False,
        ),
        (
            "with patch.multiple('app', client=sentinel) as clients:\n        clients.assert_called_once()",
            False,
        ),
        (
            "constructor = AsyncMock\n    client = constructor()\n    client.assert_not_awaited()",
            True,
        ),
    ],
)
def test_interaction_assertions_require_known_mock_receivers(
    body: str, recognized: bool
) -> None:
    result = _analyze(
        "from unittest.mock import Mock, AsyncMock, patch\ndef test_run():\n    " + body
    )
    assert result.tests[0].has_assertion([]) is recognized


@pytest.mark.parametrize(
    ("source", "recognized"),
    [
        (
            "from unittest.mock import AsyncMock as AM\ndef test_run(client: AM): client.assert_awaited_once()",
            True,
        ),
        (
            "from unittest.mock import AsyncMock\ndef test_run(client: 'AsyncMock'): client.assert_awaited_once()",
            True,
        ),
        (
            "import pytest\nfrom unittest.mock import AsyncMock\n@pytest.fixture\ndef client() -> AsyncMock: return AsyncMock()\ndef test_run(client): client.assert_awaited_once()",
            True,
        ),
        (
            "import pytest\n@pytest.fixture\ndef client():\n    assert ready\n    return unknown\ndef test_run(client): client.assert_called_once()",
            False,
        ),
        ("def test_run(client): client.assert_awaited_once()", False),
    ],
)
def test_typed_mock_fixtures_supply_receiver_types_only(
    source: str, recognized: bool
) -> None:
    assert _analyze(source).tests[0].has_assertion([]) is recognized


@pytest.mark.parametrize(
    ("body", "recognized"),
    [
        (
            "closed = asyncio.Event()\n    await asyncio.wait_for(closed.wait(), timeout=1)",
            True,
        ),
        ("closed = asyncio.Event()\n    await closed.wait()", False),
        (
            "closed = asyncio.Event()\n    await asyncio.wait_for(closed.wait(), timeout=None)",
            False,
        ),
        ("await asyncio.wait_for(app.wait(), timeout=1)", False),
        ("await asyncio.wait_for(command(), timeout=1)", False),
        (
            "closed = asyncio.Event()\n    await asyncio.wait_for(closed.wait(), 1)",
            True,
        ),
        (
            "closed = asyncio.Event()\n    asyncio.wait_for(closed.wait(), timeout=1)",
            False,
        ),
        (
            "closed = asyncio.Event()\n    async with asyncio.timeout(1):\n        await closed.wait()",
            True,
        ),
        (
            "closed = asyncio.Event()\n    async with asyncio.timeout_at(when=deadline):\n        await closed.wait()",
            True,
        ),
        (
            "closed = asyncio.Event()\n    async with asyncio.timeout(None):\n        await closed.wait()",
            False,
        ),
        (
            "closed = asyncio.Event()\n    asyncio.timeout(1)\n    await closed.wait()",
            False,
        ),
        (
            "closed = asyncio.Event()\n    async with asyncio.timeout(1):\n        pending = closed.wait()",
            False,
        ),
        (
            "closed = asyncio.Event()\n    async with asyncio.timeout(1):\n        pass\n    await closed.wait()",
            False,
        ),
        (
            "closed = asyncio.Event()\n    async with asyncio.timeout(await closed.wait()):\n        pass",
            False,
        ),
        (
            "async with asyncio.timeout(1):\n        await command()",
            False,
        ),
    ],
)
def test_bounded_lifecycle_evidence_requires_known_event(
    body: str, recognized: bool
) -> None:
    assert (
        _analyze("import asyncio\nasync def test_run():\n    " + body)
        .tests[0]
        .has_assertion([])
        is recognized
    )


@pytest.mark.parametrize(
    ("body", "recognized"),
    [
        ("pytest.raises(ValueError)", False),
        ("manager = pytest.warns(UserWarning)", False),
        ("pytest.raises(ValueError, operation)", True),
        ("with pytest.raises(ValueError):\n        operation()", True),
    ],
)
def test_unused_exception_contexts_are_not_outcome_evidence(
    body: str, recognized: bool
) -> None:
    [test] = _analyze("import pytest\ndef test_run():\n    " + body).tests
    assert test.has_assertion([]) is recognized


@pytest.mark.parametrize(
    ("fixture", "recognized"),
    [
        ("@pytest.fixture\ndef client(): return AsyncMock()", True),
        ("@pytest.fixture(name='client')\ndef transport(): return AsyncMock()", True),
        (
            "@pytest.fixture\ndef client():\n    mock = AsyncMock()\n    yield mock",
            True,
        ),
        (
            "@pytest.fixture\ndef client():\n    if condition:\n        return unknown()\n    return AsyncMock()",
            False,
        ),
        (
            "@pytest.fixture\ndef client():\n    if condition:\n        return AsyncMock()",
            False,
        ),
        (
            "@pytest.fixture\ndef client():\n    def helper(): return AsyncMock()\n    return helper()",
            False,
        ),
    ],
)
def test_fixture_results_supply_only_consistent_known_types(
    fixture: str, recognized: bool
) -> None:
    [test] = _analyze(
        "import pytest\nfrom unittest.mock import AsyncMock\n"
        + fixture
        + "\ndef test_run(client): client.assert_awaited_once()"
    ).tests
    assert test.has_assertion([]) is recognized


def test_class_fixture_types_do_not_leak_to_other_test_classes() -> None:
    module = _analyze("""
import pytest
from unittest.mock import AsyncMock
class TestFirst:
    @pytest.fixture
    def client(self): return AsyncMock()
    def test_run(self, client): client.assert_awaited_once()
class TestSecond:
    def test_run(self, client): client.assert_awaited_once()
""")
    assert [test.has_assertion([]) for test in module.tests] == [True, False]
