from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

ONLY_SMT103 = CheckOptions(select=("SMT103",))


def _project(tmp_path: Path, third_party: str, source: str) -> Path:
    config = f"""
version: 1
project:
  root_packages: [app]
architecture:
  layers:
    domain:
      path: domain
      third_party: {third_party}
"""
    return write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/domain/model.py": source,
        },
    )


class TestDefaultDeny:
    policy = "{default: deny, allow: [pydantic, sqlalchemy.orm]}"

    def test_allowed_package_and_submodules(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            self.policy,
            "import pydantic.fields\nfrom sqlalchemy.orm import Mapped\n",
        )

        assert violations(root, ONLY_SMT103) == []

    def test_standard_library_is_never_third_party(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            self.policy,
            "from __future__ import annotations\nimport os\nimport json.decoder\n",
        )

        assert violations(root, ONLY_SMT103) == []

    def test_other_package_lists_what_is_allowed(self, tmp_path: Path) -> None:
        root = _project(tmp_path, self.policy, "import requests\n")

        [found] = violations(root, ONLY_SMT103)

        assert (
            found.message
            == "domain must not import requests (domain allows only: pydantic, sqlalchemy.orm)"
        )

    def test_parent_of_allowed_submodule_is_denied(self, tmp_path: Path) -> None:
        root = _project(tmp_path, self.policy, "from sqlalchemy import text\n")

        assert [v.code for v in violations(root, ONLY_SMT103)] == ["SMT103"]

    def test_short_form_deny_allows_nothing(self, tmp_path: Path) -> None:
        root = _project(tmp_path, "deny", "import attrs\n")

        [found] = violations(root, ONLY_SMT103)

        assert (
            found.message
            == "domain must not import attrs (domain allows no third-party packages)"
        )


class TestDefaultAllow:
    def test_denied_package(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            "{default: allow, deny: [fastapi]}",
            "from fastapi.routing import APIRouter\nimport httpx\n",
        )

        [found] = violations(root, ONLY_SMT103)

        assert (found.line, found.message) == (
            1,
            "domain must not import fastapi (denied: fastapi)",
        )
