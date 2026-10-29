import ast

import pytest

from smelt.analysis.test_outcomes import vacuous_outcomes
from smelt.analysis.test_syntax import analyze_test_syntax


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        (
            "for name, content in generate().items():\n        assert committed[name] == content",
            True,
        ),
        ("for path in directory.iterdir():\n        assert valid(path)", True),
        ("assert all(valid(row) for row in query())", True),
        ("assert all(results)", True),
        ("assert all(valid(row) for row in [1, 2] if eligible(row))", True),
        ("assert paths\n    assert all(valid(path) for path in paths)", False),
        ("assert len(rows) == 2\n    assert all(valid(row) for row in rows)", False),
        ("assert generate() == committed", False),
        ("assert result == []", False),
        ("assert not rows or all(valid(row) for row in rows)", False),
        ("for value in [1, 2]:\n        assert valid(value)", False),
        ("assert all(valid(value) for value in (1, 2))", False),
        (
            "for first in [1, 2]:\n        for second in result:\n            assert valid(first, second)",
            True,
        ),
        (
            "for value in result:\n        def helper():\n            assert valid(value)",
            False,
        ),
        ("for value in result:\n        client.assert_called_with(value)", False),
        (
            "client = Mock()\n    for value in result:\n        client.assert_called_with(value)",
            True,
        ),
        ("assert True", False),
        ("all = custom\n    assert all(rows)", False),
        (
            "operations = (first, second)\n    for operation in operations:\n        assert operation()",
            False,
        ),
        ("values = (1, 2)\n    assert all(valid(value) for value in values)", False),
        (
            "values = [1, 2]\n    values.clear()\n    for value in values:\n        assert valid(value)",
            True,
        ),
        ("for value in range(2):\n        assert valid(value)", False),
        ("for value in range(3, 0, -1):\n        assert valid(value)", False),
        ("for value in range(0):\n        assert valid(value)", True),
        (
            "range = dynamic_range\n    for value in range(2):\n        assert valid(value)",
            True,
        ),
        (
            "values = [1, 2]\n    values = generate()\n    for value in values:\n        assert valid(value)",
            True,
        ),
        (
            "for value in generate():\n        run(value)\n    else:\n        assert completed",
            False,
        ),
    ],
)
def test_vacuous_outcomes_and_intentional_counterexamples(
    body: str, flagged: bool
) -> None:
    module = analyze_test_syntax(
        ast.parse("from unittest.mock import Mock\ndef test_run():\n    " + body),
        "tests/test_run.py",
    )
    risks = vacuous_outcomes(module.tests[0], [])
    assert bool(risks) is flagged
    if risks:
        assert all(node.lineno >= 3 for node in risks)
