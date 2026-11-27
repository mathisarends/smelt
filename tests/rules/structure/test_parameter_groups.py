from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

OPTIONS = CheckOptions(select=("CPX009",))
CONFIG = (
    "version: 2\nproject: {root_packages: [app]}\n"
    "rules: {complexity/duplicate-parameter-group: hint}\n"
)
GROUP = (
    "event_name: str, event_type: type, max_queue_size: int, overflow, timeout_seconds"
)
ARGUMENTS = "event_name, event_type, max_queue_size, overflow, timeout_seconds"


def _check(tmp_path: Path, files: dict[str, str]) -> list[Violation]:
    root = write_project(
        tmp_path, {"smelt.yml": CONFIG, "app/__init__.py": "", **files}
    )
    return violations(root, OPTIONS)


def test_a_group_forwarded_across_modules_is_reported_once(tmp_path: Path) -> None:
    [finding] = _check(
        tmp_path,
        {
            "app/client.py": f"class Client:\n    def listen_all(self, {GROUP}): ...\n",
            "app/router.py": f"class Router:\n    def route(self, {GROUP}, extra): ...\n",
            "app/source.py": f"def listen({GROUP}): ...\n",
        },
    )
    assert (finding.path, finding.line) == ("app/client.py", 2)
    assert finding.severity.value == "hint"
    assert "travel together through 3 functions in 3 modules" in finding.message
    assert finding.expected is not None
    assert finding.expected["parameters"] == [
        "event_name",
        "event_type",
        "max_queue_size",
        "overflow",
        "timeout_seconds",
    ]
    assert finding.expected["families"] == 3
    assert finding.expected["annotations"]["event_name"] == "str"
    assert finding.expected["annotations"]["overflow"] is None
    assert [location["function"] for location in finding.expected["locations"]] == [
        "Client.listen_all",
        "Router.route",
        "listen",
    ]


def test_an_override_family_stays_quiet(tmp_path: Path) -> None:
    assert (
        _check(
            tmp_path,
            {
                "app/port.py": "from typing import Protocol\n\n"
                f"class Browser(Protocol):\n    def key(self, {GROUP}) -> None: ...\n",
                "app/chrome.py": f"class Chrome:\n    def key(self, {GROUP}) -> None: ...\n",
                "app/firefox.py": "from app.port import Browser\n\n"
                f"class Firefox(Browser):\n    def key(self, {GROUP}) -> None: ...\n",
            },
        )
        == []
    )


def test_overlapping_groups_keep_all_members_in_source_order(tmp_path: Path) -> None:
    found = _check(
        tmp_path,
        {
            "app/events.py": f"def first({GROUP}, channel): ...\n"
            f"def second({GROUP}, origin): ...\n"
            f"def third({GROUP}, channel): ...\n"
            f"def fourth({GROUP}, channel, origin): ...\n"
            f"def fifth({GROUP}, origin): ...\n"
        },
    )
    assert [
        (entry["parameters"], [loc["function"] for loc in entry["locations"]])
        for finding in found
        if (entry := finding.expected) is not None
    ] == [
        (sorted([*ARGUMENTS.split(", "), "channel"]), ["first", "third", "fourth"]),
        (
            sorted(ARGUMENTS.split(", ")),
            ["first", "second", "third", "fourth", "fifth"],
        ),
        (sorted([*ARGUMENTS.split(", "), "origin"]), ["second", "fourth", "fifth"]),
    ]


def test_a_di_provider_and_super_forwarding_join_the_constructor(
    tmp_path: Path,
) -> None:
    assert (
        _check(
            tmp_path,
            {
                "app/service.py": f"class Service:\n    def __init__(self, {GROUP}): ...\n",
                "app/providers.py": "from app.service import Service\n\n"
                f"class Provider:\n    def service(self, {GROUP}) -> Service:\n"
                f"        return Service({ARGUMENTS})\n",
                "app/facade.py": "from app.service import Service\n\n"
                f"class Facade(Service):\n    def __init__(self, {GROUP}):\n"
                f"        super().__init__({ARGUMENTS})\n\n"
                f"    @classmethod\n    def create(cls, {GROUP}):\n"
                f"        return cls({ARGUMENTS})\n",
            },
        )
        == []
    )


def test_forwarding_wrappers_join_their_callee(tmp_path: Path) -> None:
    keywords = ", ".join(f"{name}={name}" for name in ARGUMENTS.split(", "))
    assert (
        _check(
            tmp_path,
            {
                "app/events.py": f"def listen({GROUP}):\n    return _listen({keywords})\n\n"
                f"def _listen({GROUP}): ...\n",
                "app/client.py": "from app.events import listen\n\n"
                f"class Client:\n    def listen(self, {GROUP}):\n"
                f"        return listen({ARGUMENTS})\n",
            },
        )
        == []
    )


def test_a_factory_called_through_its_class_joins_the_constructor(
    tmp_path: Path,
) -> None:
    keywords = ", ".join(f"{name}={name}" for name in ARGUMENTS.split(", "))
    assert (
        _check(
            tmp_path,
            {
                "app/entity.py": f"class Entity:\n    def __init__(self, *, {GROUP}): ...\n\n"
                f"    @classmethod\n    def create(cls, *, {GROUP}):\n"
                f"        return cls({keywords})\n",
                "app/service.py": "from app.entity import Entity\n\n"
                f"class Service:\n    def _built(self, *, {GROUP}) -> Entity:\n"
                f"        return Entity.create({keywords})\n",
            },
        )
        == []
    )


def test_groups_below_the_thresholds_stay_quiet(tmp_path: Path) -> None:
    four = "event_name, event_type, max_queue_size, overflow"
    assert (
        _check(
            tmp_path,
            {
                "app/one.py": f"def first({four}, a): ...\ndef second({four}, b): ...\n",
                "app/two.py": f"def third({four}, c): ...\n",
                "app/three.py": f"def fourth({GROUP}): ...\ndef fifth({GROUP}): ...\n",
            },
        )
        == []
    )


def test_receivers_variadics_overloads_and_tests_are_ignored(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "app/shapes.py": "from typing import overload\n\n"
            "class A:\n    def run(self, a, b, c, d, *args, **kwargs): ...\n"
            "class B:\n    def go(cls, a, b, c, d, *args, **kwargs): ...\n"
            "def walk(self, a, b, c, d, *args, **kwargs): ...\n"
            f"@overload\ndef stub({GROUP}): ...\n"
            f"@overload\ndef other({GROUP}): ...\n"
            f"def real({GROUP}): ...\n",
            "tests/test_shapes.py": f"def test_one({GROUP}): ...\n"
            f"def test_two({GROUP}): ...\n",
        },
    )
    assert violations(root, OPTIONS) == []


def test_the_rule_is_opt_in(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/one.py": f"def first({GROUP}): ...\ndef second({GROUP}): ...\n",
            "app/two.py": f"def third({GROUP}): ...\n",
        },
    )
    assert [
        item for item in violations(root, CheckOptions()) if item.code == "CPX009"
    ] == []
