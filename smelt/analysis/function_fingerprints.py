"""Behavior fingerprints of module-level functions, cached per file content."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.analysis.parsing import tree_nodes

if TYPE_CHECKING:
    from smelt.analysis.parsing import AstCache

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

# Smaller bodies are too generic to be worth a shared module.
_MIN_STATEMENTS = 3


@dataclass(frozen=True, slots=True)
class FunctionFingerprint:
    name: str
    line: int
    column: int
    fingerprint: str


_FINGERPRINTS = TypeAdapter(tuple[FunctionFingerprint, ...])


def function_fingerprints(asts: AstCache, path: str) -> tuple[FunctionFingerprint, ...]:
    """Fingerprints of the module-level functions in ``path`` with enough statements."""

    def build() -> tuple[FunctionFingerprint, ...]:
        return tuple(
            FunctionFingerprint(
                function.name, function.lineno, function.col_offset + 1, fingerprint
            )
            for function in asts.parse(path).body
            if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
            and (fingerprint := _fingerprint(function)) is not None
        )

    if asts.cache is None:
        return build()
    return asts.cache.get(
        "function-fingerprints",
        asts.files.content_id(path),
        _FINGERPRINTS,
        build=build,
    )


def _fingerprint(function: _Function) -> str | None:
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    statements = sum(
        isinstance(node, ast.stmt)
        for statement in body
        for node in tree_nodes(statement)
    )
    if statements < _MIN_STATEMENTS:
        return None
    # Names, docstrings and positions do not change what the function does.
    shape = type(function)(
        name="",
        args=function.args,
        body=body,
        decorator_list=function.decorator_list,
        returns=function.returns,
        type_params=function.type_params,
    )
    return hashlib.sha256(ast.dump(shape).encode()).hexdigest()[:16]
