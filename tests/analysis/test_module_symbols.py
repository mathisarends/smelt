from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.context import AnalysisContext
from smelt.analysis.module_symbols import ModuleSymbol, internal_module_symbols
from smelt.config import load_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def _symbols(
    root: Path, source: str, extra: dict[str, str] | None = None
) -> list[ModuleSymbol]:
    write_project(
        root,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/models.py": source,
            **(extra or {}),
        },
    )
    context = AnalysisContext(root, load_config(root / "smelt.yaml").config)
    return list(internal_module_symbols(context.syntax, context.member_usage))


def test_internal_constants_and_type_aliases_include_locations_and_uses(
    tmp_path: Path,
) -> None:
    found = _symbols(
        tmp_path,
        "MIN_LENGTH = 8\ntype Payload = str | bytes\ndef _valid(value: Payload):\n    return len(value) >= MIN_LENGTH\n",
    )
    assert [
        (item.node.id, item.kind, item.node.lineno, item.internal_references)
        for item in found
    ] == [
        ("MIN_LENGTH", "constant", 1, (4,)),
        ("Payload", "type-alias", 2, (3,)),
    ]


@pytest.mark.parametrize(
    "api",
    [
        "def read(value: Payload) -> Payload: ...",
        'def read(value: "Payload") -> "Payload": ...',
        "async def read(*values: Payload, **options: Payload) -> Payload: ...",
        "class Secret:\n    value: Payload",
        'class Secret:\n    value: "Payload"',
        'class Secret:\n    def __init__(self):\n        self.value: Payload = ""',
        "if ENABLED:\n    def read(value: Payload): ...",
        "def read[T: Payload](value: T): ...",
        "class Secret[T: Payload]: pass",
        'class Secret:\n    def _populate(self):\n        self.value: Payload = ""',
        "class Secret:\n    Value = Payload",
    ],
)
def test_public_annotations_preserve_transitive_aliases(
    tmp_path: Path, api: str
) -> None:
    assert (
        _symbols(tmp_path, "type Raw = str | bytes\ntype Payload = list[Raw]\n" + api)
        == []
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"app/caller.py": "from app.models import Payload"},
        {"app/__init__.py": "from .models import Payload as PublicPayload"},
        {"tests/test_models.py": "from app.models import Payload"},
    ],
)
def test_external_alias_uses_preserve_their_annotation_dependencies(
    tmp_path: Path, extra: dict[str, str]
) -> None:
    assert _symbols(tmp_path, "type Raw = str\ntype Payload = list[Raw]\n", extra) == []


def test_exported_private_function_preserves_its_signature_alias(
    tmp_path: Path,
) -> None:
    assert (
        _symbols(
            tmp_path,
            "type Payload = str\ndef _read(value: Payload): ...\n",
            {
                "app/__init__.py": "from .models import _read as read",
            },
        )
        == []
    )


@pytest.mark.parametrize(
    "source",
    [
        "VALUE = 1\nVALUE = 2",
        "VALUE = 1\ndel VALUE",
        "VALUE = 1\nfrom other import VALUE",
        "VALUE = 1\ndef _read(VALUE): return VALUE",
        "type Payload = str\ndef _read(Payload): return Payload",
        "VALUE = 1\nif condition: VALUE = 2",
        "VALUE = 1\nfrom other import *",
        "VALUE = 1\nfrom other import __all__",
        "VALUE = 1\n__all__ = build_exports()",
        "VALUE = 1\ndef _lookup(name): return globals()[name]",
        "VALUE = 1\ndef __getattr__(name): return resolve(name)",
    ],
)
def test_uncertain_bindings_and_dynamic_namespaces_are_preserved(
    tmp_path: Path, source: str
) -> None:
    assert _symbols(tmp_path, source) == []


def test_lowercase_assignments_classes_and_private_names_are_outside_scope(
    tmp_path: Path,
) -> None:
    assert (
        _symbols(
            tmp_path,
            "value = 1\n_LIMIT = 2\ntype _Payload = str\nPayload = str\nclass Empty: pass\n",
        )
        == []
    )
