import ast

import pytest

from smelt.analysis.test_awaits import call_only_async_mocks
from smelt.analysis.test_syntax import analyze_test_syntax


@pytest.mark.parametrize(
    ("body", "receivers"),
    [
        ("connect = AsyncMock()\n    connect.assert_called_once()", ["connect"]),
        ("connect = AsyncMock()\n    assert connect.called", ["connect"]),
        ("connect = AsyncMock()\n    assert connect.call_count == 1", ["connect"]),
        (
            "connect = AsyncMock()\n    assert connect.call_args.args == ('id',)",
            ["connect"],
        ),
        (
            "connect = AsyncMock()\n    args, kwargs = connect.call_args\n    assert args == ('id',)",
            ["connect"],
        ),
        (
            "connect = AsyncMock()\n    args = connect.call_args\n    values = args.args\n    assert values == ('id',)",
            ["connect"],
        ),
        ("connect = AsyncMock()\n    args = connect.call_args", []),
        (
            "connect = AsyncMock()\n    args = connect.call_args\n    args = expected\n    assert args",
            [],
        ),
        ("connect = Mock()\n    connect.assert_called_once()", []),
        (
            "connect = AsyncMock()\n    args = connect.call_args\n    for args in expected:\n        pass\n    assert args",
            [],
        ),
        ("connect = unknown()\n    connect.assert_called_once()", []),
        ("client = AsyncMock()\n    client.connect.assert_called_once()", []),
        (
            "client = AsyncMock(spec=Unknown)\n    client.connect.assert_called_once()",
            [],
        ),
        (
            "client = AsyncMock(spec=Accounts)\n    client.connect.assert_called_once()",
            ["client.connect"],
        ),
        (
            "client = Mock(spec_set=Accounts)\n    client.connect.assert_called_once()",
            ["client.connect"],
        ),
        (
            "client = create_autospec(Accounts)\n    client.connect.assert_called_once()",
            ["client.connect"],
        ),
        (
            "client = AsyncMock(spec=Accounts)\n    client.terminate.assert_called_once()",
            [],
        ),
        (
            "client = AsyncMock(spec=Accounts)\n    client.connect = Mock()\n    client.connect.assert_called_once()",
            [],
        ),
        (
            "client = AsyncMock(spec=Accounts)\n    alias = client\n    alias.connect = Mock()\n    client.connect.assert_called_once()",
            [],
        ),
        (
            "client = AsyncMock(spec=Accounts)\n    client.connect.return_value = 'ok'\n    client.connect.assert_called_once()",
            ["client.connect"],
        ),
        (
            "client = AsyncMock(spec=Accounts)\n    alias = client.connect\n    alias.assert_called_once()\n    client.connect.assert_awaited_once()",
            [],
        ),
        (
            "first = AsyncMock()\n    second = AsyncMock()\n    first.assert_called_once()\n    second.assert_awaited_once()",
            ["first"],
        ),
        (
            "connect = AsyncMock()\n    connect.assert_called_once()\n    connect = AsyncMock()\n    connect.assert_awaited_once()",
            ["connect"],
        ),
        (
            "connect = AsyncMock()\n    connect.assert_called_once()\n    assert connect.await_count == 1",
            [],
        ),
        (
            "connect = AsyncMock()\n    connect.assert_called_once()\n    expected = connect.await_args\n    assert expected.args == ('id',)",
            [],
        ),
        (
            "connect = AsyncMock()\n    connect.assert_called_once()\n    connect.assert_not_awaited()",
            [],
        ),
        ("connect = AsyncMock()\n    connect.assert_not_called()", []),
        ("connect = AsyncMock()\n    assert not connect.called", []),
        ("connect = AsyncMock()\n    assert connect.call_count == 0", []),
        ("connect = AsyncMock()\n    assert connect.call_args is None", []),
        ("connect = AsyncMock()\n    assert connect.call_args_list == []", []),
        ("connect = AsyncMock()\n    assert connect.call_args.args == ()", ["connect"]),
        (
            "connect = AsyncMock()\n    def helper():\n        connect.assert_awaited_once()\n    connect.assert_called_once()",
            ["connect"],
        ),
    ],
)
def test_call_only_review_requires_known_async_assertion_evidence(
    body: str, receivers: list[str]
) -> None:
    source = (
        "from unittest.mock import AsyncMock, Mock, create_autospec\n"
        "class Accounts:\n    async def connect(self): pass\n    def terminate(self): pass\n"
        "def test_connect():\n    " + body
    )
    [test] = analyze_test_syntax(ast.parse(source), "tests/test_accounts.py").tests
    assert [item.receiver for item in call_only_async_mocks(test)] == receivers


@pytest.mark.parametrize("annotation", ["", " -> AsyncMock"])
def test_fixture_return_keeps_known_spec_with_or_without_mock_annotation(
    annotation: str,
) -> None:
    source = (
        "import pytest\nfrom unittest.mock import AsyncMock\n"
        "class Accounts:\n    async def connect(self): pass\n"
        f"@pytest.fixture\ndef accounts(){annotation}:\n    return AsyncMock(spec=Accounts)\n"
        "def test_connect(accounts):\n    accounts.connect.assert_called_once()"
    )
    [test] = analyze_test_syntax(ast.parse(source), "tests/test_accounts.py").tests
    [finding] = call_only_async_mocks(test)
    assert finding.async_callable == "tests.test_accounts.Accounts.connect"
    assert finding.provenance == (
        "fixture-return" if annotation else "fixture-inferred"
    )


@pytest.mark.parametrize(
    ("decorator", "flagged"),
    [("", True), ("@abstractmethod\n    ", True), ("@unknown\n    ", False)],
)
def test_unknown_decorators_do_not_establish_async_spec_methods(
    decorator: str, flagged: bool
) -> None:
    source = (
        "from abc import abstractmethod\nfrom unittest.mock import AsyncMock\n"
        f"class Accounts:\n    {decorator}async def connect(self): pass\n"
        "def test_connect():\n    accounts = AsyncMock(spec=Accounts)\n    accounts.connect.assert_called_once()"
    )
    [test] = analyze_test_syntax(ast.parse(source), "tests/test_accounts.py").tests
    assert bool(call_only_async_mocks(test)) is flagged
