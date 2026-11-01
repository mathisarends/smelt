import ast

import pytest

from smelt.analysis.none_default_rewrite import rewrite_none_defaults
from smelt.analysis.none_defaults import none_defaults


@pytest.mark.parametrize(
    "fallback",
    [
        "(1 if ready else 3)",
        "(lambda: 7)",
        "(yield 7)",
        "await fallback()",
        "f'{other or 7}'",
    ],
)
def test_fallback_expression_structure_survives_rewriting(fallback: str) -> None:
    prefix = "async " if "await" in fallback else ""
    source = f"{prefix}def create(options: int | None = None):\n    return options or {fallback}\n"
    candidates = list(none_defaults(ast.parse(source), {}))
    rewritten = rewrite_none_defaults(source, candidates)
    expected = f"{prefix}def create(options: int | None = None):\n    return options if options is not None else {fallback}\n"
    assert ast.dump(ast.parse(rewritten)) == ast.dump(ast.parse(expected))


def test_default_inside_an_f_string_keeps_its_format_specification() -> None:
    source = (
        "def create(options: int | None = None):\n    return f'{options or 7:03}'\n"
    )
    rewritten = rewrite_none_defaults(
        source, list(none_defaults(ast.parse(source), {}))
    )
    expected = "def create(options: int | None = None):\n    return f'{options if options is not None else 7:03}'\n"
    assert ast.dump(ast.parse(rewritten)) == ast.dump(ast.parse(expected))
