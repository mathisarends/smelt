from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


class TestApiUsedOnlyByTests:
    def test_reports_symbols_referenced_only_from_tests(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": '__all__ = ["exported"]\n',
                "app/cache.py": (
                    "def get(key): ...\n\n\n"
                    "def reset_for_tests(): ...\n\n\n"
                    "def exported(): ...\n\n\n"
                    "def _private(): ...\n"
                ),
                "app/service.py": "from app.cache import get\n\nget('x')\n",
                "tests/test_cache.py": (
                    "from app.cache import _private, exported, get, reset_for_tests\n"
                ),
            },
        )

        found = violations(root, CheckOptions(select=("SMT408",)))

        assert codes_at(found) == [("SMT408", "app/cache.py", 4)]
        assert found[0].message == "app.cache.reset_for_tests is only used by tests"

    def test_does_not_report_registered_http_routes(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": "",
                "app/routes.py": (
                    "from fastapi import APIRouter\n"
                    "router = APIRouter()\n\n"
                    "@router.get('/items')\n"
                    "def list_items(): return []\n\n"
                    "def test_helper(): return []\n"
                ),
                "tests/test_routes.py": (
                    "from app.routes import list_items, test_helper\n"
                ),
            },
        )

        found = violations(root, CheckOptions(select=("SMT408",)))

        assert codes_at(found) == [("SMT408", "app/routes.py", 7)]
