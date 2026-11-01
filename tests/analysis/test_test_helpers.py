import ast
import textwrap

import pytest

from smelt.analysis.test_outcomes import vacuous_outcomes
from smelt.analysis.test_syntax import (
    _analyze_test_syntax,  # smelt: ignore[SMT420] -- Verify helper execution and binding facts.
)
from smelt.analysis.test_timing import unbounded_waits


@pytest.mark.parametrize(
    ("source", "outcome", "waits"),
    [
        (
            """
            import subprocess
            def verify():
                result = subprocess.run(['compiler'], check=False)
                assert result.returncode == 0
            def test_run():
                verify()
            """,
            True,
            1,
        ),
        (
            """
            def test_run():
                verify()
            def verify():
                assert result == 1
            """,
            True,
            0,
        ),
        (
            """
            import subprocess
            def test_run():
                def verify():
                    result = subprocess.run(['compiler'], timeout=10, check=False)
                    assert result.returncode == 0
                alias = verify
                alias()
            """,
            True,
            0,
        ),
        (
            """
            import asyncio
            def test_run():
                async def verify():
                    await asyncio.Event().wait()
                    assert result == 1
                asyncio.run(verify())
            """,
            True,
            1,
        ),
        (
            """
            import asyncio
            async def test_run():
                async def verify():
                    await asyncio.Event().wait()
                    assert result == 1
                await verify()
            """,
            True,
            1,
        ),
        (
            """
            from asyncio import run as execute
            def test_run():
                async def verify():
                    assert result == 1
                execute(verify())
            """,
            True,
            0,
        ),
        (
            """
            import subprocess
            def test_run():
                def verify():
                    subprocess.run(['compiler'], check=False)
                    assert result == 1
            """,
            False,
            0,
        ),
        (
            """
            import asyncio
            def test_run():
                async def verify():
                    await asyncio.Event().wait()
                    assert result == 1
                verify()
            """,
            False,
            0,
        ),
        (
            """
            def test_run():
                def verify():
                    assert result == 1
                register_callback(verify)
            """,
            False,
            0,
        ),
        (
            """
            def test_run():
                def verify():
                    assert result == 1
                    yield result
                verify()
            """,
            False,
            0,
        ),
        (
            """
            def test_run():
                @decorator
                def verify():
                    assert result == 1
                verify()
            """,
            False,
            0,
        ),
        (
            """
            def test_run():
                def verify():
                    assert result == 1
                verify = replacement
                verify()
            """,
            False,
            0,
        ),
        (
            """
            def verify():
                assert result == 1
            def test_run(verify):
                verify()
            """,
            False,
            0,
        ),
        (
            """
            import asyncio
            def test_run():
                async def verify():
                    assert result == 1
                asyncio = replacement
                asyncio.run(verify())
            """,
            False,
            0,
        ),
        (
            """
            def test_run():
                def verify():
                    assert result == 1
                try:
                    verify()
                except AssertionError:
                    pass
            """,
            False,
            0,
        ),
        (
            """
            def test_run():
                def verify():
                    try:
                        assert result == 1
                    except AssertionError:
                        pass
                verify()
            """,
            False,
            0,
        ),
        (
            """
            def test_run():
                def verify():
                    assert result == 1
                checks = (verify() for row in values)
            """,
            False,
            0,
        ),
        (
            """
            def inner():
                assert result == 1
            def outer():
                inner()
            def test_run():
                outer()
            """,
            False,
            0,
        ),
        (
            """
            def verify():
                assert result == 1
            def test_run():
                verify()
            from external import *
            """,
            False,
            0,
        ),
        (
            """
            def verify():
                assert result == 1
            def test_run():
                verify()
            match external:
                case {'verify': verify}:
                    pass
            """,
            False,
            0,
        ),
        (
            """
            import subprocess
            def test_run():
                execute = subprocess.run
                def verify():
                    execute(['compiler'], check=False)
                    assert result == 1
                execute = replacement
                verify()
            """,
            False,
            0,
        ),
    ],
)
def test_direct_helpers_and_nonexecuted_neighbors(
    source: str, outcome: bool, waits: int
) -> None:
    module = _analyze_test_syntax(
        ast.parse(textwrap.dedent(source)), "tests/test_run.py"
    )
    [test] = module.tests
    assert test.has_assertion([]) is outcome
    assert len(unbounded_waits(test, [])) == waits


@pytest.mark.parametrize(
    "body",
    [
        "await asyncio.wait_for(verify(), 1)",
        "async with asyncio.timeout(1):\n        await verify()",
    ],
)
@pytest.mark.parametrize("process", [False, True])
def test_caller_async_deadline_guards_async_waits_but_not_processes(
    body: str, process: bool
) -> None:
    operation = (
        "subprocess.run(['compiler'], check=False)"
        if process
        else "await asyncio.Event().wait()"
    )
    source = (
        "import asyncio\nimport subprocess\n"
        "async def verify():\n    " + operation + "\n    assert result == 1\n"
        "async def test_run():\n    " + body + "\n"
    )
    [test] = _analyze_test_syntax(ast.parse(source), "tests/test_run.py").tests
    assert test.has_assertion([])
    waits = unbounded_waits(test, [])
    assert bool(waits) is process
    if waits:
        assert waits[0].helper == "verify"
        assert waits[0].helper_call_line is not None


@pytest.mark.parametrize("caller_loop", [False, True])
def test_helper_assertions_retain_possibly_empty_iteration(caller_loop: bool) -> None:
    source = (
        "def verify():\n    assert all(valid(row) for row in rows)\n"
        "def test_run():\n"
        + (
            "    for row in rows:\n        verify()\n"
            if caller_loop
            else "    verify()\n"
        )
    )
    [test] = _analyze_test_syntax(ast.parse(source), "tests/test_run.py").tests
    assert test.has_assertion([])
    assert vacuous_outcomes(test, [])


def test_repeated_helper_names_in_separate_tests_are_distinct() -> None:
    source = """
def test_first():
    def verify():
        assert result == 1
    verify()
def test_second():
    def verify():
        pass
    verify()
"""
    first, second = _analyze_test_syntax(ast.parse(source), "tests/test_run.py").tests
    assert first.has_assertion([])
    assert not second.has_assertion([])


def test_repeated_helper_calls_report_each_unbounded_operation_once() -> None:
    source = """
import asyncio
async def verify():
    await asyncio.Event().wait()
async def test_run():
    await asyncio.wait_for(verify(), 1)
    await verify()
    await verify()
"""
    [test] = _analyze_test_syntax(ast.parse(source), "tests/test_run.py").tests
    [wait] = unbounded_waits(test, [])
    assert wait.node.lineno == 4
    assert wait.helper_call_line == 7
    assert wait.helper_call_lines == (7, 8)


def test_recursive_helpers_do_not_expand_transitively() -> None:
    source = "def verify():\n    verify()\ndef test_run():\n    verify()\n"
    [test] = _analyze_test_syntax(ast.parse(source), "tests/test_run.py").tests
    assert not test.has_assertion([])
    assert unbounded_waits(test, []) == []


def test_helper_default_executes_before_its_callers_deadline() -> None:
    source = """
import asyncio
async def test_run():
    async def verify(value=await asyncio.Event().wait()):
        assert result == 1
    await asyncio.wait_for(verify(), 1)
"""
    [test] = _analyze_test_syntax(ast.parse(source), "tests/test_run.py").tests
    assert test.has_assertion([])
    [wait] = unbounded_waits(test, [])
    assert wait.node.lineno == 4
    assert wait.helper is None
