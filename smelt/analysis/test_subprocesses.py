"""Static outcome evidence in checked, inline Python subprocesses."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_CHECKED = frozenset(
    {"subprocess.run", "subprocess.check_call", "subprocess.check_output"}
)
_FLAGS = frozenset({"-P", "-I", "-B", "-u"})


def checked_python_assertions(
    call: ast.Call,
    qualified: str,
    resolve: Callable[[ast.AST], str | None],
    text: Callable[[ast.expr], str | None],
) -> bool:
    if not _checked_options(call, qualified, text):
        return False
    command = (
        call.args[0]
        if call.args
        else next((kw.value for kw in call.keywords if kw.arg == "args"), None)
    )
    source = _python_source(command, resolve, text)
    if source is None:
        return False
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return False
    return any(
        isinstance(node, ast.Assert)
        and not (isinstance(node.test, ast.Constant) and node.test.value is True)
        for node in tree.body
    )


def child_status_propagates(call: ast.Call, parents: Mapping[ast.AST, ast.AST]) -> bool:
    node: ast.AST = call
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.Try | ast.TryStar | ast.With | ast.AsyncWith):
            return False
    return True


_COMMAND_PARTS = 3
_SCRIPT_ARGUMENTS = 2


def _checked_options(
    call: ast.Call, qualified: str, text: Callable[[ast.expr], str | None]
) -> bool:
    if qualified not in _CHECKED or any(kw.arg is None for kw in call.keywords):
        return False
    options = {kw.arg: kw.value for kw in call.keywords}
    check = options.get("check")
    if qualified == "subprocess.run" and not (
        isinstance(check, ast.Constant) and check.value is True
    ):
        return False
    if "executable" in options or (
        "shell" in options
        and not (
            isinstance(options["shell"], ast.Constant)
            and options["shell"].value is False
        )
    ):
        return False
    environment = options.get("env")
    return not (
        environment is not None
        and (
            not isinstance(environment, ast.Dict)
            or any(
                text(key) == "PYTHONOPTIMIZE" and text(value) not in {"", "0"}
                for key, value in zip(environment.keys, environment.values, strict=True)
                if key is not None
            )
        )
    )


def _python_source(
    command: ast.expr | None,
    resolve: Callable[[ast.AST], str | None],
    text: Callable[[ast.expr], str | None],
) -> str | None:
    if (
        not isinstance(command, ast.List | ast.Tuple)
        or len(command.elts) < _COMMAND_PARTS
    ):
        return None
    interpreter, *arguments = command.elts
    if resolve(interpreter) != "sys.executable":
        return None
    while arguments and text(arguments[0]) in _FLAGS:
        arguments.pop(0)
    if len(arguments) != _SCRIPT_ARGUMENTS or text(arguments[0]) != "-c":
        return None
    return text(arguments[1])
