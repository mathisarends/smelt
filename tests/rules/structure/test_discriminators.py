from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT324",))
SOURCE = """from dataclasses import dataclass, field
from typing import Literal

@dataclass
class CheckpointExtension:
    tag: str
    data: dict[str, object]
    type: Literal["checkpoint.extension"] = field(default="checkpoint.extension", kw_only=True)
"""


def _project(tmp_path: Path, source: str = SOURCE, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/events.py": source},
    )


def test_reports_the_checkpoint_discriminator_without_changing_source(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    before = (root / "app/events.py").read_bytes()

    [finding] = violations(root, OPTIONS)

    assert finding.code == "SMT324"
    assert finding.rule == "prefer-str-enum-discriminator"
    assert finding.severity is Severity.HINT
    assert (finding.path, finding.line, finding.column) == ("app/events.py", 8, 5)
    assert (finding.end_line, finding.end_column) == (
        8,
        len(SOURCE.splitlines()[7]) + 1,
    )
    assert finding.source_module == "app.events"
    assert finding.expected is not None
    assert finding.expected["class"] == "CheckpointExtension"
    assert finding.expected["field"] == "type"
    assert finding.expected["value"] == "checkpoint.extension"
    assert finding.expected["automatic_rewrite"] is False
    assert finding.hint is not None
    assert "Literal[EnumType.MEMBER]" in finding.hint
    assert (root / "app/events.py").read_bytes() == before


@pytest.mark.parametrize("field", ["type", "kind", "tag"])
@pytest.mark.parametrize(
    ("imports", "annotation"),
    [
        ("from typing import Literal", 'Literal["started"]'),
        ("from typing import Literal as L", 'L["started"]'),
        ("import typing", 'typing.Literal["started"]'),
        ("import typing as t", 't.Literal["started"]'),
        ("from typing_extensions import Literal", 'Literal["started"]'),
        ("import typing_extensions as t", 't.Literal["started"]'),
        (
            "from typing import Literal, Annotated as A",
            'A[Literal["started"], "metadata"]',
        ),
        (
            "from typing import Literal, Required",
            'Required[Literal["started"]]',
        ),
        (
            "from typing_extensions import Literal, NotRequired, Annotated",
            'NotRequired[Annotated[Literal["started"], "metadata"]]',
        ),
        ("from typing import Literal", "\"Literal['started']\""),
    ],
)
def test_conventional_fields_typing_aliases_and_wrappers(
    tmp_path: Path, field: str, imports: str, annotation: str
) -> None:
    root = _project(tmp_path, f"{imports}\nclass Started:\n    {field}: {annotation}\n")

    [finding] = violations(root, OPTIONS)

    assert finding.expected is not None
    assert finding.expected["field"] == field
    assert finding.expected["value"] == "started"


@pytest.mark.parametrize(
    "declaration",
    [
        'class Started:\n    kind: Literal["started"] = "started"',
        'class Started(BaseModel):\n    type: Literal["started"] = Field(default="started")',
        'class Started(TypedDict):\n    tag: Required[Literal["started"]]',
        'def create():\n    class Started:\n        kind: Literal["started"]',
        'class Events:\n    class Started:\n        kind: Literal["started"]',
    ],
)
def test_model_shapes_and_defaults(tmp_path: Path, declaration: str) -> None:
    root = _project(
        tmp_path,
        "from typing import Literal, TypedDict, Required\n"
        "from pydantic import BaseModel, Field\n" + declaration,
    )

    [finding] = violations(root, OPTIONS)

    assert finding.expected is not None
    assert finding.expected["class"] == "Started"
    assert finding.expected["value"] == "started"


@pytest.mark.parametrize(
    "source",
    [
        'from typing import Literal\nclass Options:\n    type: Literal["a", "b"]',
        "from typing import Literal\nclass Options:\n    type: Literal[1]",
        "from typing import Literal\nclass Options:\n    type: Literal[True]",
        'from typing import Literal\nclass Options:\n    type: Literal[b"a"]',
        'from typing import Literal\nclass Options:\n    status: Literal["a"]',
        'from typing import Literal, ClassVar\nclass Options:\n    type: ClassVar[Literal["a"]]',
        'from typing import Literal\nclass Options:\n    type: list[Literal["a"]]',
        'from typing import Literal\nclass Options:\n    type: Literal["a"] | None',
        'from typing import Literal\nclass Options:\n    type: str = "a"',
        'from typing import Literal\ntype Tag = Literal["a"]\nclass Options:\n    type: Tag',
        'from typing import Literal\ntype: Literal["a"] = "a"',
        'from typing import Literal\ndef create(type: Literal["a"]): pass',
        'from typing import Literal\ndef create():\n    type: Literal["a"] = "a"',
        'from typing import Literal\nclass Options:\n    def create(self):\n        type: Literal["a"] = "a"',
        'from typing import Literal\nclass Options:\n    type: "not valid Python!"',
        'from external import Literal\nclass Options:\n    type: Literal["a"]',
        'class Options:\n    type: Literal["a"]',
        'from .typing import Literal\nclass Options:\n    type: Literal["a"]',
    ],
)
def test_general_literals_and_unproven_bindings_are_valid(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_string_discriminators_are_reported_for_each_union_variant(
    tmp_path: Path,
) -> None:
    source = """from typing import Literal
class Started:
    type: Literal["started"] = "started"
class Finished:
    type: Literal["finished"] = "finished"
type Event = Started | Finished
"""

    found = violations(_project(tmp_path, source), OPTIONS)

    assert [finding.line for finding in found] == [3, 5]
    assert [finding.expected["value"] for finding in found if finding.expected] == [
        "started",
        "finished",
    ]


def test_shared_str_enum_literals_are_valid_for_a_union(tmp_path: Path) -> None:
    source = """from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

class EventType(StrEnum):
    CHECKPOINT_EXTENSION = "checkpoint.extension"
    CHECKPOINT_FINISHED = "checkpoint.finished"

@dataclass
class CheckpointExtension:
    type: Literal[EventType.CHECKPOINT_EXTENSION] = field(default=EventType.CHECKPOINT_EXTENSION, kw_only=True)

@dataclass
class CheckpointFinished:
    type: Literal[EventType.CHECKPOINT_FINISHED] = EventType.CHECKPOINT_FINISHED

type Checkpoint = CheckpointExtension | CheckpointFinished
"""

    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize(
    "shadow",
    [
        "Literal = object()",
        "del Literal",
        "class Literal: pass",
        "def Literal(): pass",
        "def create(Literal): pass",
        "from external import Literal",
        "from external import *",
        "try:\n    run()\nexcept Exception as Literal:\n    pass",
        "match value:\n    case {'name': Literal}: pass",
        "def create[Literal](): pass",
    ],
)
def test_shadowed_literal_imports_are_uncertain(tmp_path: Path, shadow: str) -> None:
    source = (
        f'from typing import Literal\n{shadow}\nclass Event:\n    type: Literal["a"]'
    )

    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize(
    "shadow",
    [
        "t = other",
        "t.Literal = other",
        "del t.Literal",
        "def create(t): pass",
    ],
)
def test_shadowed_typing_modules_are_uncertain(tmp_path: Path, shadow: str) -> None:
    source = f'import typing as t\n{shadow}\nclass Event:\n    type: t.Literal["a"]'

    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_rule_is_off_by_default(tmp_path: Path) -> None:
    assert violations(_project(tmp_path)) == []


@pytest.mark.parametrize("name", ["SMT324", "prefer-str-enum-discriminator"])
def test_explicit_rule_settings_enable_the_hint(tmp_path: Path, name: str) -> None:
    [finding] = violations(
        _project(tmp_path, config=CONFIG + f"rules: {{{name}: hint}}\n")
    )

    assert finding.code == "SMT324"


@pytest.mark.parametrize(
    ("config", "options"),
    [
        (CONFIG + "rules: {SMT324: off}\n", OPTIONS),
        (CONFIG, CheckOptions(select=("SMT324",), ignore=("SMT324",))),
        (
            CONFIG
            + "rules: {prefer-str-enum-discriminator: error}\n"
            + "structure:\n  contracts:\n    external-schema:\n"
            + "      match: app\n      rules: {prefer-str-enum-discriminator: off}\n",
            OPTIONS,
        ),
    ],
)
def test_explicit_disable_ignore_and_package_overrides(
    tmp_path: Path, config: str, options: CheckOptions
) -> None:
    assert violations(_project(tmp_path, config=config), options) == []


def test_intentional_schema_strings_can_be_suppressed(tmp_path: Path) -> None:
    config = CONFIG + "rules: {prefer-str-enum-discriminator: error}\n"
    root = _project(tmp_path, config=config)
    assert check(root).report.failed
    source = root / "app/events.py"
    source.write_text(
        SOURCE.rstrip()
        + "  # smelt: ignore[SMT324] -- External schema requires a raw string\n",
        encoding="utf-8",
    )

    outcome = check(root)

    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1


@pytest.mark.parametrize(("version", "count"), [("3.10", 0), ("3.11", 1), ("3.12", 1)])
def test_declared_python_minimum(tmp_path: Path, version: str, count: int) -> None:
    config = CONFIG.replace(
        "root_packages: [app]", f"root_packages: [app], python_version: '{version}'"
    )

    found = violations(_project(tmp_path, config=config), OPTIONS)

    assert len(found) == count


def test_python_metadata_changes_invalidate_cached_findings(tmp_path: Path) -> None:
    root = _project(tmp_path)
    metadata = root / "pyproject.toml"
    metadata.write_text('[project]\nrequires-python = ">=3.11"\n', encoding="utf-8")
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["python"]["minimum"] == "3.11"

    metadata.write_text('[project]\nrequires-python = ">=3.10"\n', encoding="utf-8")

    assert violations(root, OPTIONS) == []
    assert violations(root, CheckOptions(select=("SMT324",), use_cache=False)) == []


def test_editing_a_discriminator_invalidates_cached_findings(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert len(violations(root, OPTIONS)) == 1
    (root / "app/events.py").write_text(
        SOURCE.replace(
            'Literal["checkpoint.extension"]', "Literal[EventType.CHECKPOINT_EXTENSION]"
        ),
        encoding="utf-8",
    )

    assert violations(root, OPTIONS) == []
    assert violations(root, CheckOptions(select=("SMT324",), use_cache=False)) == []
