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
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
tests:
  patching:
    forbid: [domain, application, private]
"""

SOURCES = {
    "smelt.yaml": CONFIG,
    "app/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/domain/clock.py": "def now() -> int:\n    return 0\n",
    "app/application/__init__.py": "",
    "app/application/service.py": "class Service:\n    def run(self) -> None: ...\n",
}

PATCH_TESTS = """
import time
from unittest import mock
from unittest.mock import patch

import requests

from app.application.service import Service
from app.domain import clock


@patch("app.domain.clock.now")
def test_decorated(now) -> None: ...


def test_boundaries(monkeypatch, mocker) -> None:
    monkeypatch.setenv("TOKEN", "x")
    monkeypatch.setattr(time, "time", lambda: 0)
    mocker.patch("requests.get")
    with mock.patch.dict("os.environ", {"A": "1"}):
        pass


def test_internals(monkeypatch, mocker) -> None:
    monkeypatch.setattr(clock, "now", lambda: 1)
    mocker.patch.object(Service, "run")
    monkeypatch.setattr("requests._internal", None)
"""


class TestPatchesInternal:
    def test_reports_forbidden_categories_only(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path, {**SOURCES, "tests/test_service.py": PATCH_TESTS}
        )

        found = violations(root, CheckOptions(select=("SMT402",)))

        assert [(v.line, v.message) for v in found] == [
            (11, "patches app.domain.clock.now (domain)"),
            (24, "patches app.domain.clock.now (domain)"),
            (25, "patches app.application.service.Service.run (application)"),
            (26, "patches requests._internal (private)"),
        ]

    def test_forbid_list_can_be_emptied(self, tmp_path: Path) -> None:
        config = CONFIG.replace("forbid: [domain, application, private]", "forbid: []")
        root = write_project(
            tmp_path,
            {**SOURCES, "smelt.yaml": config, "tests/test_service.py": PATCH_TESTS},
        )

        assert violations(root, CheckOptions(select=("SMT402",))) == []


class TestPrivateAccess:
    def test_reads_writes_and_imports(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/test_service.py": (
                    "import sys\n"
                    "from app.domain.clock import _cache\n\n\n"
                    "class FakeClock:\n"
                    "    def __init__(self) -> None:\n"
                    "        self._now = 0\n\n\n"
                    "def test_service(service) -> None:\n"
                    "    service._repository = None\n"
                    "    assert service.state._asdict() == {}\n"
                    "    sys._getframe()\n"
                ),
            },
        )

        found = violations(root, CheckOptions(select=("SMT403",)))

        assert codes_at(found) == [
            ("SMT403", "tests/test_service.py", 2),
            ("SMT403", "tests/test_service.py", 11),
        ]
        assert found[0].message == "imports private name _cache from app.domain.clock"

    def test_patching_private_attribute_reports_only_smt402(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/test_service.py": (
                    "def test_service(monkeypatch, service) -> None:\n"
                    '    monkeypatch.setattr(service, "_repo", None); service._repo\n'
                ),
            },
        )

        found = violations(root, CheckOptions(select=("SMT402", "SMT403")))

        assert [v.code for v in found] == ["SMT402"]

    def test_allowed_by_config(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                **SOURCES,
                "smelt.yaml": CONFIG + "  private_access: allow\n",
                "tests/test_service.py": "def test_x(s) -> None:\n    s._x = 1\n",
            },
        )

        assert violations(root, CheckOptions(select=("SMT403",))) == []
