import ast

import pytest

from smelt.analysis.test_syntax import TestModule as SyntaxModule
from smelt.analysis.test_syntax import analyze_test_syntax


def _analyze(source: str) -> SyntaxModule:
    return analyze_test_syntax(ast.parse(source), "tests/test_service.py")


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
