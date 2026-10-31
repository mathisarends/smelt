import ast

import pytest

from smelt.analysis.test_cases import independent_case_loops
from smelt.analysis.test_syntax import (
    _analyze_test_syntax,  # smelt: ignore[SMT420] -- Unit-test this parser boundary.
)


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        (
            "for path in ['app/a.py', 'app/b.py']:\n        selected = select(path)\n        assert selected == ['app']",
            True,
        ),
        (
            "for source, expected in [('a', 1), ('b', 2)]:\n        result = parse(source)\n        assert result == expected",
            True,
        ),
        (
            "for source in inputs:\n        result = parse(source)\n        assert result",
            False,
        ),
        (
            "for source in ['one']:\n        result = parse(source)\n        assert result",
            False,
        ),
        (
            "for event in ['take', 'release']:\n        controller.apply(event)\n        assert controller.owner == event",
            False,
        ),
        (
            "for event in ['take', 'release']:\n        state = advance(state, event)\n        assert state.owner == event",
            False,
        ),
        (
            "for event in ['take', 'release']:\n        results.append(apply(event))\n        assert results",
            False,
        ),
        (
            "for source in ['a', 'b']:\n        with transaction():\n            result = save(source)\n            assert result",
            False,
        ),
        (
            "for path in ['a', 'b']:\n        if path:\n            result = select(path)\n            assert result",
            False,
        ),
        (
            "first = resolve('model')\n    second = resolve('model')\n    assert first is second",
            False,
        ),
    ],
)
def test_case_candidates_preserve_replay_and_lifecycle_loops(
    body: str, flagged: bool
) -> None:
    [test] = _analyze_test_syntax(
        ast.parse("def test_run():\n    " + body), "tests/test_run.py"
    ).tests
    assert bool(independent_case_loops(test)) is flagged


def test_action_can_be_observed_directly_in_the_assertion() -> None:
    [test] = _analyze_test_syntax(
        ast.parse(
            "def test_run():\n    for path in ['app/a.py', 'app/b.py']:\n        assert selected(path)"
        ),
        "tests/test_run.py",
    ).tests
    assert len(independent_case_loops(test)) == 1
