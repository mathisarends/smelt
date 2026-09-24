from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [gw]
architecture:
  features:
    root: gw.features
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infra, may_depend_on: [domain, application]}
roles:
  port:
    detect: {base: typing.Protocol}
    layers: [application]
    file: ports.py
  adapter:
    detect: {implements: port}
    layers: [infrastructure]
"""

ROLE_RULES = CheckOptions(select=("SMT204", "SMT303"))


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    base = {
        "smelt.yaml": CONFIG,
        "gw/__init__.py": "",
        "gw/features/__init__.py": "",
        "gw/features/voice/__init__.py": "",
        "gw/features/voice/domain/__init__.py": "",
        "gw/features/voice/application/__init__.py": "",
        "gw/features/voice/infra/__init__.py": "",
    }
    return write_project(tmp_path, {**base, **files})


PORTS = """
from typing import Protocol


class Sessions(Protocol):
    def save(self) -> None: ...
"""


class TestMisplacedRole:
    def test_roles_in_their_layers_are_fine(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "gw/features/voice/application/ports.py": PORTS,
                "gw/features/voice/infra/sql.py": (
                    "from gw.features.voice.application.ports import Sessions\n\n\n"
                    "class SqlSessions(Sessions):\n"
                    "    def save(self) -> None: ...\n"
                ),
            },
        )

        assert violations(root, ROLE_RULES) == []

    def test_port_in_infrastructure(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"gw/features/voice/infra/sql.py": PORTS})

        [found] = violations(root, ROLE_RULES)

        assert (found.code, found.line, found.column, found.end_column) == (
            "SMT204",
            4,
            7,
            15,
        )
        assert found.message == (
            "port Sessions is defined in infrastructure; ports belong in application"
        )
        assert found.hint == "Move Sessions to voice/application/ports.py."

    def test_adapter_detected_through_implements(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "gw/features/voice/application/ports.py": PORTS,
                "gw/features/voice/domain/sessions.py": (
                    "from gw.features.voice.application import ports\n\n\n"
                    "class MemorySessions(ports.Sessions):\n"
                    "    def save(self) -> None: ...\n"
                ),
            },
        )

        found = violations(root, ROLE_RULES)

        assert codes_at(found) == [
            ("SMT204", "gw/features/voice/domain/sessions.py", 4)
        ]
        assert found[0].expected == {
            "layers": ["infrastructure"],
            "path": "voice/infra/",
        }

    def test_pep_695_generics(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "gw/features/voice/application/ports.py": (
                    "from typing import Protocol\n\n"
                    "type Key = str\n\n\n"
                    "class Repository[T](Protocol):\n"
                    "    def get(self, key: Key) -> T: ...\n"
                ),
                "gw/features/voice/domain/memory.py": (
                    "from gw.features.voice.application.ports import Repository\n\n\n"
                    "class MemoryRepository[T](Repository[T]):\n"
                    "    def get(self, key: str) -> T: ...\n"
                ),
            },
        )

        found = violations(root, ROLE_RULES)

        assert codes_at(found) == [("SMT204", "gw/features/voice/domain/memory.py", 4)]
        assert found[0].message.startswith(
            "adapter MemoryRepository is defined in domain"
        )


class TestRoleFile:
    def test_port_outside_ports_module(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"gw/features/voice/application/session.py": PORTS})

        [found] = violations(root, ROLE_RULES)

        assert found.code == "SMT303"
        assert found.message == (
            "port Sessions must be defined in voice/application/ports.py"
        )

    def test_ports_package_counts_as_the_file(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "gw/features/voice/application/ports/__init__.py": "",
                "gw/features/voice/application/ports/sessions.py": PORTS,
            },
        )

        assert violations(root, ROLE_RULES) == []
