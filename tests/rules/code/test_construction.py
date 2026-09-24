from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
architecture:
  composition_root: [app.bootstrap]
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infra, may_depend_on: [domain, application]}
  imports:
    type_checking: ignore
roles:
  port:
    detect: {base: typing.Protocol}
    layers: [application]
  adapter:
    detect: {implements: port}
    layers: [infrastructure]
"""

BASE = {
    "smelt.yaml": CONFIG,
    "app/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/application/__init__.py": "",
    "app/application/ports.py": (
        "from typing import Protocol\n\n\nclass Sessions(Protocol):\n"
        "    def save(self) -> None: ...\n"
    ),
    "app/infra/__init__.py": "",
    "app/infra/sql.py": (
        "from app.application.ports import Sessions\n\n\n"
        "class SqlSessions(Sessions):\n"
        "    def save(self) -> None: ...\n\n\n"
        "def default() -> SqlSessions:\n"
        "    return SqlSessions()\n"
    ),
}


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    return write_project(tmp_path, {**BASE, **files})


class TestConcreteConstruction:
    def test_composition_root_and_own_layer_may_construct(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/bootstrap.py": "from app.infra import sql\n\nsessions = sql.SqlSessions()\n"
            },
        )

        assert violations(root, CheckOptions(select=("SMT201",))) == []

    def test_construction_in_application(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/start.py": (
                    "from typing import TYPE_CHECKING\n\n"
                    "if TYPE_CHECKING:\n"
                    "    from app.infra.sql import SqlSessions\n\n\n"
                    "def start() -> None:\n"
                    "    from app.infra.sql import SqlSessions\n"
                    "    SqlSessions().save()\n"
                )
            },
        )

        found = violations(root, CheckOptions(select=("SMT201",)))

        assert codes_at(found) == [("SMT201", "app/application/start.py", 9)]
        assert found[0].message == (
            "adapter SqlSessions is constructed in application; construct it in the "
            "composition root (app.bootstrap)"
        )
        assert (
            found[0].hint == "Depend on Sessions and wire SqlSessions in app.bootstrap."
        )
        assert (found[0].column, found[0].end_column) == (5, 16)

    def test_layer_boundary_wins_over_construction(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/start.py": (
                    "from app.infra.sql import SqlSessions\n\nsessions = SqlSessions()\n"
                )
            },
        )

        found = violations(root, CheckOptions(select=("SMT101", "SMT201")))

        assert [v.code for v in found] == ["SMT101"]

    def test_inactive_without_implementation_roles(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "smelt.yaml": CONFIG.split("roles:", maxsplit=1)[0],
                "app/application/start.py": (
                    "def start() -> None:\n"
                    "    from app.infra.sql import SqlSessions\n"
                    "    SqlSessions()\n"
                ),
            },
        )

        assert violations(root, CheckOptions(select=("SMT201",))) == []


class TestConcreteDependency:
    def test_parameter_annotated_with_adapter(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/start.py": (
                    "from typing import TYPE_CHECKING\n\n"
                    "if TYPE_CHECKING:\n"
                    "    from app.infra import sql\n\n\n"
                    "class Start:\n"
                    "    def __init__(self, sessions: 'sql.SqlSessions | None') -> None:\n"
                    "        self.sessions = sessions\n"
                )
            },
        )

        [found] = violations(root, CheckOptions(select=("SMT202",)))

        assert found.line == 8
        assert found.message == (
            "parameter sessions of Start.__init__ is annotated with adapter "
            "SqlSessions; depend on Sessions"
        )
        assert found.expected == {"ports": ["app.application.ports.Sessions"]}

    def test_unquoted_type_checking_annotation(self, tmp_path: Path) -> None:
        # Python 3.14 style (PEP 649): no future import, no quotes.
        root = _project(
            tmp_path,
            {
                "app/application/start.py": (
                    "from typing import TYPE_CHECKING\n\n"
                    "if TYPE_CHECKING:\n"
                    "    from app.infra.sql import SqlSessions\n\n\n"
                    "def start(sessions: SqlSessions) -> None: ...\n"
                )
            },
        )

        [found] = violations(root, CheckOptions(select=("SMT202",)))

        assert (found.line, found.message) == (
            7,
            "parameter sessions of start is annotated with adapter SqlSessions; "
            "depend on Sessions",
        )

    def test_port_annotation_is_fine(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/start.py": (
                    "from app.application.ports import Sessions\n\n\n"
                    "def start(sessions: Sessions) -> None: ...\n"
                )
            },
        )

        assert violations(root, CheckOptions(select=("SMT202",))) == []

    def test_adapter_layer_may_use_adapters(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {})

        assert violations(root, CheckOptions(select=("SMT202",))) == []
