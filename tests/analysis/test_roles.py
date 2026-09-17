from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext
from smelt.config import load_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [gw]
architecture:
  layers:
    app: {path: app}
    infra: {path: infra, may_depend_on: [app]}
roles:
  port:
    detect: {base: typing.Protocol}
    layers: [app]
  adapter:
    detect: {implements: port}
    layers: [infra]
"""

SOURCES = {
    "smelt.yaml": CONFIG,
    "gw/__init__.py": "",
    "gw/app/__init__.py": "",
    "gw/app/ports.py": """
    from typing import Protocol


    class Sessions(Protocol):
        def save(self) -> None: ...
    """,
    "gw/infra/__init__.py": "",
    "gw/infra/sql.py": """
    from gw.app.ports import Sessions


    class SqlSessions(Sessions):
        def save(self) -> None: ...


    class DuckTypedSessions:
        def save(self) -> None: ...
    """,
}

PORT = "gw.app.ports.Sessions"
DUCK = "gw.infra.sql.DuckTypedSessions"


class _StructuralTypes:
    """A TypeIndex that says yes to the pairs it was given."""

    def __init__(self, implemented: set[tuple[str, str]]) -> None:
        self.implemented = implemented
        self.asked: list[tuple[str, str]] = []

    def resolve_type(self, path: str, line: int, column: int) -> str | None:
        del path, line, column
        return None

    def prepare(self, pairs: Iterable[tuple[str, str]]) -> None:
        self.asked.extend(pairs)

    def implements(self, cls: str, protocol: str) -> bool:
        return (cls, protocol) in self.implemented


def _context(tmp_path: Path, types: _StructuralTypes | None = None) -> AnalysisContext:
    root = write_project(tmp_path, SOURCES)
    loaded = load_config(root / "smelt.yaml")
    return AnalysisContext(root, loaded.config, types=types)


class TestNominalDetection:
    def test_subclassing_a_port_makes_an_adapter(self, tmp_path: Path) -> None:
        roles = _context(tmp_path).roles

        assert roles.roles_of(PORT) == frozenset({"port"})
        assert roles.roles_of("gw.infra.sql.SqlSessions") == frozenset({"adapter"})

    def test_a_duck_typed_class_has_no_role(self, tmp_path: Path) -> None:
        assert _context(tmp_path).roles.roles_of(DUCK) == frozenset()


class TestStructuralDetection:
    def test_type_information_finds_the_duck_typed_adapter(
        self, tmp_path: Path
    ) -> None:
        types = _StructuralTypes({(DUCK, PORT)})

        roles = _context(tmp_path, types).roles

        assert roles.roles_of(DUCK) == frozenset({"adapter"})
        assert [info.name for info in roles.classes_with("adapter")] == [
            "DuckTypedSessions",
            "SqlSessions",
        ]

    def test_ports_are_never_asked_about_themselves(self, tmp_path: Path) -> None:
        types = _StructuralTypes(set())

        _context(tmp_path, types).roles.matches()

        assert (PORT, PORT) not in types.asked
        assert (DUCK, PORT) in types.asked

    def test_nothing_changes_when_the_backend_says_no(self, tmp_path: Path) -> None:
        types = _StructuralTypes(set())

        roles = _context(tmp_path, types).roles

        assert roles.roles_of(DUCK) == frozenset()
