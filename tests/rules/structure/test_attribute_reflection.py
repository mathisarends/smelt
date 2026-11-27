from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

OPTIONS = CheckOptions(select=("IDM009",))
CONFIG = "version: 2\nproject: {root_packages: [app]}\nrules: {idioms/redundant-getattr: hint}\n"
MODELS = """from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class Entity:
    created_at: datetime


@dataclass
class UserMessage(Entity):
    text: str


@dataclass
class AssistantMessage(Entity):
    model: str


class Plain:
    kind = "plain"
    label: str

    def __init__(self, owner: UserMessage) -> None:
        self.owner = owner
        if owner.text:
            self.maybe = 1


class Dynamic:
    kind = "dynamic"

    def __getattr__(self, name: str) -> object:
        return name


class Slotted:
    __slots__ = ("kind",)


class Named(Protocol):
    name: str


type Message = UserMessage | AssistantMessage
"""


def _service(tmp_path: Path, source: str) -> list[Violation]:
    header = (
        "from collections.abc import Sequence\n"
        "from typing import Any\n\n"
        "from app.models import (\n"
        "    AssistantMessage, Dynamic, Entity, Message, Named, Plain, Slotted, UserMessage,\n"
        ")\n\n"
    )
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "app/models.py": MODELS,
            "app/service.py": header + source,
        },
    )
    return violations(root, OPTIONS)


def test_a_parameter_of_a_project_class_reports_the_declared_attribute(
    tmp_path: Path,
) -> None:
    [finding] = _service(
        tmp_path,
        'def read(message: UserMessage) -> object:\n    return getattr(message, "text", None)\n',
    )
    assert finding.severity.value == "hint"
    assert (finding.path, finding.line) == ("app/service.py", 9)
    assert "read `message.text` directly" in finding.message
    assert finding.expected == {
        "call": "getattr",
        "receiver": "message",
        "attribute": "text",
        "receiver_types": ["app.models.UserMessage"],
        "declared_by": ["app.models.UserMessage"],
        "default": True,
        "automatic_rewrite": False,
    }


def test_a_loop_target_over_a_union_collection_needs_every_member(
    tmp_path: Path,
) -> None:
    [finding] = _service(
        tmp_path,
        "def latest(entries: tuple[UserMessage | AssistantMessage, ...]) -> None:\n"
        "    for entry in entries:\n"
        '        getattr(entry, "created_at", None)\n',
    )
    assert finding.expected is not None
    assert finding.expected["receiver_types"] == [
        "app.models.UserMessage",
        "app.models.AssistantMessage",
    ]
    assert finding.expected["declared_by"] == ["app.models.Entity"] * 2
    assert "each declares `created_at`" in finding.message


@pytest.mark.parametrize(
    "source",
    [
        'def read(message: Message) -> None:\n    getattr(message, "created_at")\n',
        'def read(message: "Message") -> None:\n    hasattr(message, "created_at")\n',
        "def read(messages: Sequence[Message]) -> None:\n"
        "    for message in messages:\n"
        '        getattr(message, "created_at", None)\n',
        'def read(plain: Plain) -> None:\n    getattr(plain.owner, "text", None)\n',
        'def read() -> None:\n    message = UserMessage(text="")\n    getattr(message, "text", None)\n',
        'def read(named: Named) -> None:\n    getattr(named, "name", None)\n',
        "class Reader:\n"
        "    def __init__(self, message: UserMessage) -> None:\n"
        "        self.message = message\n\n"
        "    def read(self) -> None:\n"
        '        getattr(self.message, "text", None)\n'
        '        hasattr(self, "message")\n',
    ],
    ids=[
        "alias",
        "quoted-alias",
        "alias-sequence",
        "attribute",
        "constructor",
        "protocol",
        "self",
    ],
)
def test_supported_receiver_sources_are_reported(tmp_path: Path, source: str) -> None:
    assert _service(tmp_path, source) != []


def test_hasattr_is_reported_as_always_true(tmp_path: Path) -> None:
    [finding] = _service(
        tmp_path,
        'def read(message: UserMessage) -> bool:\n    return hasattr(message, "created_at")\n',
    )
    assert finding.expected is not None
    assert finding.expected["call"] == "hasattr"
    assert finding.expected["declared_by"] == ["app.models.Entity"]
    assert "always true" in finding.message


@pytest.mark.parametrize(
    "source",
    [
        'def read(value: Any) -> None:\n    getattr(value, "text", None)\n',
        'def read(value: object) -> None:\n    getattr(value, "text", None)\n',
        'def read(value) -> None:\n    getattr(value, "text", None)\n',
        'def read[T: UserMessage](value: T) -> None:\n    getattr(value, "text", None)\n',
        'def read(value: Named) -> None:\n    getattr(value, "text", None)\n',
        'def read(value: Sequence) -> None:\n    getattr(value, "text", None)\n',
        'def read(value: Message) -> None:\n    getattr(value, "text", None)\n',
        'def read(value: UserMessage | None) -> None:\n    getattr(value, "text", None)\n',
        'def read(value: UserMessage) -> None:\n    getattr(value, "__dict__", None)\n',
        'def read(value: Dynamic) -> None:\n    getattr(value, "kind", None)\n',
        'def read(value: Slotted) -> None:\n    getattr(value, "kind", None)\n',
        'def read(value: Plain) -> None:\n    getattr(value, "label", None)\n',
        'def read(value: Plain) -> None:\n    getattr(value, "maybe", None)\n',
        "def read(value: UserMessage, name: str) -> None:\n    getattr(value, name, None)\n",
        "def read(value: UserMessage) -> None:\n"
        "    value = load()\n"
        '    getattr(value, "text", None)\n',
        "def read(value: UserMessage) -> None:\n"
        "    getattr = print\n"
        '    getattr(value, "text", None)\n',
        "class Mixin:\n"
        "    def read(self) -> None:\n"
        '        getattr(self, "text", None)\n',
        "class Reader:\n"
        "    kind = 1\n\n"
        "    def __init__(self) -> None:\n"
        '        hasattr(self, "kind")\n',
    ],
    ids=[
        "any",
        "object",
        "unannotated",
        "type-parameter",
        "protocol-without-attribute",
        "outside-class",
        "union-member-without-attribute",
        "optional",
        "dunder",
        "getattr-hook",
        "slots",
        "plain-bare-annotation",
        "conditional-init-assignment",
        "dynamic-name",
        "rebound-receiver",
        "shadowed-getattr",
        "mixin-self",
        "construction-method",
    ],
)
def test_receivers_without_a_guaranteed_attribute_stay_quiet(
    tmp_path: Path, source: str
) -> None:
    assert _service(tmp_path, source) == []


def test_tests_are_not_reviewed(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "app/models.py": MODELS,
            "tests/test_models.py": "from app.models import UserMessage\n\n"
            "def test_read(message: UserMessage) -> None:\n"
            '    assert getattr(message, "text", None)\n',
        },
    )
    assert violations(root, OPTIONS) == []


def test_the_rule_is_opt_in(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/models.py": MODELS,
            "app/service.py": "from app.models import UserMessage\n\n"
            "def read(message: UserMessage) -> None:\n"
            '    getattr(message, "text", None)\n',
        },
    )
    assert [
        item for item in violations(root, CheckOptions()) if item.code == "IDM009"
    ] == []
