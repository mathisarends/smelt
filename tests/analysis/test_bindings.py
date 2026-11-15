from __future__ import annotations

import ast

import pytest

from smelt.analysis.bindings import binding_counts, dotted_name, qualified_name


def _expression(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def test_binding_counts_cover_every_binding_form() -> None:
    tree = ast.parse(
        "import os.path\n"
        "from typing import Any as A\n"
        "x = 1\n"
        "def f(arg):\n"
        "    global g\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as error:\n"
        "        pass\n"
        "class C: ...\n"
        "match x:\n"
        "    case {'k': v, **rest}: pass\n"
        "x.attribute = 2\n"
    )

    bound = binding_counts(tree)

    assert {name: bound[name] for name in ("os", "A", "f", "arg", "g", "error")} == {
        "os": 1,
        "A": 1,
        "f": 1,
        "arg": 1,
        "g": 1,
        "error": 1,
    }
    assert (bound["C"], bound["v"], bound["rest"], bound["x"]) == (1, 1, 1, 2)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("cm", "contextlib.contextmanager"),
        ("contextlib.asynccontextmanager", "contextlib.asynccontextmanager"),
        ("unknown.attribute", None),
        ("call().attribute", None),
    ],
)
def test_qualified_name_resolves_known_bindings(
    source: str, expected: str | None
) -> None:
    bindings = {"cm": "contextlib.contextmanager", "contextlib": "contextlib"}

    assert qualified_name(_expression(source), bindings) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("dataclass", "dataclass"),
        ("dataclasses.dataclass", "dataclasses.dataclass"),
        ("call().attribute", ""),
        ("items[0]", ""),
    ],
)
def test_dotted_name_spells_name_chains(source: str, expected: str) -> None:
    assert dotted_name(_expression(source)) == expected
