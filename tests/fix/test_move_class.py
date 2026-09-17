from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config import load_config
from smelt.engine.check import CheckOptions
from smelt.engine.fix import run_fix
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.engine.fix import FixOutcome

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

BASE = {
    "smelt.yaml": CONFIG,
    "gw/__init__.py": "",
    "gw/features/__init__.py": "",
    "gw/features/voice/__init__.py": "",
    "gw/features/voice/domain/__init__.py": "",
    "gw/features/voice/application/__init__.py": "",
    "gw/features/voice/infra/__init__.py": "",
}

SESSION = """
from typing import Protocol


class Sessions(Protocol):
    def save(self) -> None: ...
"""

SESSION_MODULE = "gw/features/voice/application/session.py"
PORTS_MODULE = "gw/features/voice/application/ports.py"


def _fix(tmp_path: Path, files: dict[str, str]) -> tuple[Path, FixOutcome]:
    root = write_project(tmp_path, {**BASE, **files})
    outcome = run_fix(load_config(root / "smelt.yaml"), ("SMT303",))
    return root, outcome


def _read(root: Path, path: str) -> str:
    return (root / path).read_text(encoding="utf-8")


class TestMoveIntoANewModule:
    def test_class_and_its_imports_move(self, tmp_path: Path) -> None:
        root, outcome = _fix(tmp_path, {SESSION_MODULE: SESSION})

        assert [v.code for v in outcome.applied] == ["SMT303"]
        assert _read(root, PORTS_MODULE) == SESSION.lstrip("\n")
        assert _read(root, SESSION_MODULE) == ""
        assert violations(root) == []

    def test_importers_point_at_the_new_module(self, tmp_path: Path) -> None:
        root, outcome = _fix(
            tmp_path,
            {
                SESSION_MODULE: SESSION,
                "gw/features/voice/infra/sql.py": """
                from gw.features.voice.application.session import Sessions


                class SqlSessions(Sessions):
                    def save(self) -> None: ...
                """,
            },
        )

        assert outcome.fixes == 1
        assert _read(root, "gw/features/voice/infra/sql.py").startswith(
            "from gw.features.voice.application.ports import Sessions\n"
        )
        assert violations(root) == []

    def test_type_checking_imports_stay_type_checking(self, tmp_path: Path) -> None:
        root, _ = _fix(
            tmp_path,
            {
                "gw/features/voice/domain/calls.py": "class Call: ...\n",
                SESSION_MODULE: """
                from typing import TYPE_CHECKING, Protocol

                if TYPE_CHECKING:
                    from gw.features.voice.domain.calls import Call


                class Sessions(Protocol):
                    def save(self, call: Call) -> None: ...
                """,
            },
        )

        assert _read(root, PORTS_MODULE) == (
            "from typing import TYPE_CHECKING, Protocol\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from gw.features.voice.domain.calls import Call\n"
            "\n"
            "\n"
            "class Sessions(Protocol):\n"
            "    def save(self, call: Call) -> None: ...\n"
        )
        assert _read(root, SESSION_MODULE) == ""


class TestMoveIntoAnExistingModule:
    def test_class_is_appended_and_imports_are_reused(self, tmp_path: Path) -> None:
        root, outcome = _fix(
            tmp_path,
            {
                PORTS_MODULE: """
                from typing import Protocol


                class Calls(Protocol):
                    def dial(self) -> None: ...
                """,
                SESSION_MODULE: SESSION,
            },
        )

        assert outcome.fixes == 1
        assert _read(root, PORTS_MODULE) == (
            "from typing import Protocol\n"
            "\n"
            "\n"
            "class Calls(Protocol):\n"
            "    def dial(self) -> None: ...\n"
            "\n"
            "\n"
            "class Sessions(Protocol):\n"
            "    def save(self) -> None: ...\n"
        )
        assert violations(root) == []

    def test_missing_imports_are_added(self, tmp_path: Path) -> None:
        root, _ = _fix(
            tmp_path,
            {
                PORTS_MODULE: "CALLS = 1\n",
                SESSION_MODULE: SESSION,
            },
        )

        assert _read(root, PORTS_MODULE) == (
            "from typing import Protocol\n"
            "CALLS = 1\n"
            "\n"
            "\n"
            "class Sessions(Protocol):\n"
            "    def save(self) -> None: ...\n"
        )


class TestRefusals:
    def test_attribute_access_is_not_rewritten(self, tmp_path: Path) -> None:
        root, outcome = _fix(
            tmp_path,
            {
                SESSION_MODULE: SESSION,
                "gw/features/voice/infra/sql.py": """
                from gw.features.voice.application import session


                class SqlSessions(session.Sessions):
                    def save(self) -> None: ...
                """,
            },
        )

        assert outcome.applied == []
        assert _read(root, SESSION_MODULE) == SESSION.lstrip("\n")
        assert not (root / PORTS_MODULE).exists()

    def test_name_taken_in_the_target_module(self, tmp_path: Path) -> None:
        root, outcome = _fix(
            tmp_path,
            {
                PORTS_MODULE: "class Sessions: ...\n",
                SESSION_MODULE: SESSION,
            },
        )

        assert outcome.applied == []
        assert _read(root, PORTS_MODULE) == "class Sessions: ...\n"

    def test_violation_is_still_reported_without_a_fix(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {**BASE, PORTS_MODULE: "class Sessions: ...\n", SESSION_MODULE: SESSION},
        )

        found = violations(root, CheckOptions(select=("SMT303",)))

        assert [(v.code, v.fix) for v in found] == [("SMT303", None)]
