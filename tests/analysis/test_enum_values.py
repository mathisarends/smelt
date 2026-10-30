from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.context import AnalysisContext
from smelt.analysis.enum_values import str_enum_value_accesses
from smelt.config.models import SmeltConfig
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def _accesses(root: Path, files: dict[str, str]) -> list[tuple[str, str, str]]:
    write_project(root, {"app/__init__.py": "", **files})
    ctx = AnalysisContext(
        root,
        SmeltConfig.model_validate(
            {"version": 1, "project": {"root_packages": ["app"]}}
        ),
    )
    return [
        (access.path, access.enum, access.member)
        for access in str_enum_value_accesses(ctx.syntax)
    ]


@pytest.mark.parametrize(
    ("imports", "base", "members", "member"),
    [
        ("from enum import StrEnum", "StrEnum", 'WHEEL = "mouseWheel"', "WHEEL"),
        ("from enum import StrEnum as SE", "SE", 'wheel = "mouseWheel"', "wheel"),
        ("import enum", "enum.StrEnum", 'WHEEL = "mouseWheel"', "WHEEL"),
        ("import enum as e", "e.StrEnum", 'WHEEL = "mouseWheel"', "WHEEL"),
        ("from enum import StrEnum, auto", "StrEnum", "WHEEL = auto()", "WHEEL"),
        ("import enum as e", "e.StrEnum", "WHEEL = e.auto()", "WHEEL"),
        ("from enum import StrEnum, auto as a", "StrEnum", "WHEEL = a()", "WHEEL"),
        ("from enum import StrEnum", "StrEnum", 'WHEEL: str = "mouseWheel"', "WHEEL"),
        (
            "from enum import StrEnum",
            "StrEnum",
            'WHEEL = "mouseWheel"\n    ALIAS = WHEEL',
            "ALIAS",
        ),
    ],
)
def test_recognizes_only_known_standard_string_members(
    tmp_path: Path, imports: str, base: str, members: str, member: str
) -> None:
    source = (
        f"{imports}\nclass Kind({base}):\n    {members}\nresult = Kind.{member}.value\n"
    )
    assert _accesses(tmp_path, {"app/events.py": source}) == [
        ("app/events.py", "app.events.Kind", member)
    ]


@pytest.mark.parametrize(
    ("imports", "access"),
    [
        ("from .events import Kind", "Kind.WHEEL.value"),
        ("from app.events import Kind as K", "K.WHEEL.value"),
        ("import app.events as events", "events.Kind.WHEEL.value"),
        ("import app.events", "app.events.Kind.WHEEL.value"),
        ("from .facade import EventKind", "EventKind.WHEEL.value"),
        ("from app import facade as f", "f.EventKind.WHEEL.value"),
    ],
)
def test_resolves_aliased_relative_and_reexported_classes(
    tmp_path: Path, imports: str, access: str
) -> None:
    assert _accesses(
        tmp_path,
        {
            "app/events.py": 'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\n',
            "app/facade/__init__.py": "from ..events import Kind as EventKind\n",
            "app/consumer.py": f"{imports}\nresult = {access}\n",
        },
    ) == [("app/consumer.py", "app.events.Kind", "WHEEL")]


@pytest.mark.parametrize(
    "source",
    [
        'from enum import Enum\nclass Kind(Enum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        'from enum import Enum\nclass Kind(str, Enum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        'from external import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        'class StrEnum: pass\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\nStrEnum = other\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nKind = other\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\ndef f(Kind):\n    return Kind.WHEEL.value',
        'import enum as e\nclass Kind(e.StrEnum):\n    WHEEL = "mouseWheel"\ndef f(e):\n    return Kind.WHEEL.value',
        'from enum import StrEnum\nfrom external import *\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\n@decorate\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum, metaclass=Meta):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
        "from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = dynamic()\nresult = Kind.WHEEL.value",
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.MISSING.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\n    @property\n    def value(self): return "custom"\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\n    _ignore_ = ["WHEEL"]\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\n    def __new__(cls, value): ...\nresult = Kind.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nKind.WHEEL.value = other',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\ndel Kind.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\ndef f(event: Kind): return event.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind["WHEEL"].value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind("mouseWheel").value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.nested.WHEEL.value',
        'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\n    value = "custom"\nresult = Kind.WHEEL.value',
        'import enum\nenum.StrEnum = Other\nclass Kind(enum.StrEnum):\n    WHEEL = "mouseWheel"\nresult = Kind.WHEEL.value',
    ],
)
def test_preserves_regular_enums_unknown_bindings_and_explicit_exclusions(
    tmp_path: Path, source: str
) -> None:
    assert _accesses(tmp_path, {"app/events.py": source}) == []


def test_reexport_rebinding_and_cycles_remain_unknown(tmp_path: Path) -> None:
    assert (
        _accesses(
            tmp_path,
            {
                "app/events.py": 'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\n',
                "app/facade.py": "from .events import Kind\nKind = other\n",
                "app/a.py": "from .b import Kind\n",
                "app/b.py": "from .a import Kind\n",
                "app/consumer.py": "from .facade import Kind\nfrom .a import Kind as Other\nresult = Kind.WHEEL.value\ncycle = Other.WHEEL.value\n",
            },
        )
        == []
    )


def test_shadowed_import_in_consumer_is_unknown(tmp_path: Path) -> None:
    assert (
        _accesses(
            tmp_path,
            {
                "app/events.py": 'from enum import StrEnum\nclass Kind(StrEnum):\n    WHEEL = "mouseWheel"\n',
                "app/consumer.py": "from .events import Kind\ndef f(Kind): return Kind.WHEEL.value\n",
            },
        )
        == []
    )
