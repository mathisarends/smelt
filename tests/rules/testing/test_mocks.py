from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

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
    forbid: []
  mocks:
    max_per_test: 2
    forbid_first_party: [application]
"""

SOURCES = {
    "smelt.yaml": CONFIG,
    "app/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/domain/money.py": "class Money: ...\n",
    "app/application/__init__.py": "",
    "app/application/ports.py": "class Sessions: ...\n",
}


class TestMocksFirstParty:
    def test_spec_and_autospec_of_forbidden_layer(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/test_a.py": (
                    "from unittest.mock import Mock, create_autospec\n"
                    "from app.application.ports import Sessions\n"
                    "from app.domain.money import Money\n\n\n"
                    "def test_a(mocker) -> None:\n"
                    "    create_autospec(Sessions)\n"
                    "    mocker.MagicMock(spec=Sessions)\n"
                    "    Mock(spec=Money)\n"
                    "    Mock()\n"
                ),
            },
        )

        found = violations(root, CheckOptions(select=("SMT404",)))

        assert [(v.line, v.message) for v in found] == [
            (7, "mocks Sessions from application; use a fake implementing it"),
            (8, "mocks Sessions from application; use a fake implementing it"),
        ]


class TestTooManyMocks:
    def test_counts_fixtures_from_conftest(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/conftest.py": (
                    "import pytest\nfrom unittest.mock import Mock\n\n\n"
                    "@pytest.fixture\n"
                    "def client(settings):\n"
                    "    return Mock()\n\n\n"
                    "@pytest.fixture\n"
                    "def settings(monkeypatch):\n"
                    '    monkeypatch.setenv("A", "1")\n'
                    '    monkeypatch.setattr("app.domain.money.Money", Mock())\n'
                ),
                "tests/api/test_routes.py": (
                    "from unittest.mock import patch\n\n\n"
                    "class TestRoutes:\n"
                    '    @patch("requests.get")\n'
                    "    def test_many(self, get, client) -> None: ...\n\n"
                    "    def test_few(self, settings) -> None: ...\n"
                ),
            },
        )

        [found] = violations(root, CheckOptions(select=("SMT405",)))

        assert found.path == "tests/api/test_routes.py"
        assert found.line == 6
        assert found.message == "TestRoutes.test_many uses 4 mocks or patches (max 2)"


class TestInteractionAssertion:
    def test_boundary_mocks_are_exempt(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/test_calls.py": (
                    "from unittest.mock import patch\n\n\n"
                    '@patch("requests.post")\n'
                    "def test_calls(post, sessions, mocker) -> None:\n"
                    '    get = mocker.patch("requests.get")\n'
                    '    with patch("time.sleep") as sleep:\n'
                    "        pass\n"
                    "    post.assert_called_once()\n"
                    "    get.assert_called_once_with('x')\n"
                    "    assert sleep.call_count == 1\n"
                    "    sessions.save.assert_called_once_with(1)\n"
                    "    assert sessions.load.call_count == 2\n"
                ),
            },
        )

        found = violations(root, CheckOptions(select=("SMT406",)))

        assert [(v.line, v.message, v.severity) for v in found] == [
            (
                12,
                "asserts sessions.save.assert_called_once_with; assert on results, "
                "state or events instead",
                Severity.WARNING,
            ),
            (
                13,
                "asserts sessions.load.call_count; assert on results, state or events "
                "instead",
                Severity.WARNING,
            ),
        ]

    def test_severity_comes_from_tests_config(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                **SOURCES,
                "smelt.yaml": CONFIG + "  interaction_assertions: error\n",
                "tests/test_calls.py": "def test_a(m) -> None:\n    m.assert_called()\n",
            },
        )

        [found] = violations(root, CheckOptions(select=("SMT406",)))

        assert found.severity is Severity.ERROR
