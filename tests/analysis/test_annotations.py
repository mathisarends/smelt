import ast

import pytest

from smelt.analysis.annotations import has_postponement_evidence


@pytest.mark.parametrize(
    "source",
    [
        "VALUE = 1",
        "def f(value: int) -> str: return str(value)",
        "class User: pass\ndef f(value: User) -> None: pass",
        "import datetime\ndef f(value: datetime) -> None: pass",
    ],
)
def test_no_detected_postponement_need(source: str) -> None:
    assert not has_postponement_evidence(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "def f(value: User): pass\nclass User: pass",
        "class Node:\n    def next(self) -> Node: pass",
        "def f(value: 'User'): pass",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from other import User\ndef f(value: User): pass",
        "def f(value: list[str]): pass",
        "def f(value: int | None): pass",
        "def f(value: module.Type): pass",
        "def f(value: make_type()): pass",
        "@decorator\ndef f(value: int): pass",
        "class Model(Base):\n    value: int",
        "def f(value: int): pass\nprint(f.__annotations__)",
        "from typing import get_type_hints\ndef f(value: int): pass\nget_type_hints(f)",
        "class User: pass\ndel User\ndef f(value: User): pass",
    ],
)
def test_postponement_or_runtime_uncertainty_is_retained(source: str) -> None:
    assert has_postponement_evidence(ast.parse(source))


@pytest.mark.parametrize(
    ("source", "minimum", "evidence"),
    [
        ("def f(x: list[str]): pass", (3, 8), True),
        ("def f(x: list[str]): pass", (3, 9), False),
        ("def f(x: int | None): pass", (3, 9), True),
        ("def f(x: int | None): pass", (3, 10), False),
        ("def f(x: dict[str, list[int]] | None): pass", (3, 13), False),
        ("def f(x: unknown.Factory[str]): pass", (3, 13), True),
    ],
)
def test_modern_builtin_annotations_follow_supported_version(
    source: str, minimum: tuple[int, int], evidence: bool
) -> None:
    assert has_postponement_evidence(ast.parse(source), minimum) is evidence
