from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
architecture:
  shared: [app.shared]
  layers:
    domain:
      path: domain
      forbid_bases: [pydantic.BaseModel]
"""

ONLY_SMT203 = CheckOptions(select=("SMT203",))


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    base = {
        "smelt.yaml": CONFIG,
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/shared/__init__.py": "",
    }
    return write_project(tmp_path, {**base, **files})


class TestForbiddenBaseClass:
    def test_direct_base(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/domain/money.py": (
                    "import pydantic\n\n\nclass Money(pydantic.BaseModel): ...\n"
                )
            },
        )

        [found] = violations(root, ONLY_SMT203)

        assert found.message == (
            "domain class Money must not inherit from pydantic.BaseModel"
        )
        assert (found.line, found.column, found.end_column) == (4, 13, 31)

    def test_submodule_path_and_indirect_base(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/shared/models.py": (
                    "from pydantic.main import BaseModel\n\n\nclass Base(BaseModel): ...\n"
                ),
                "app/domain/money.py": (
                    "from app.shared.models import Base\n\n\nclass Money(Base): ...\n"
                ),
            },
        )

        [found] = violations(root, ONLY_SMT203)

        assert found.message == (
            "domain class Money must not inherit from pydantic.BaseModel "
            "(via app.shared.models.Base)"
        )

    def test_other_layers_and_plain_classes_are_fine(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/shared/models.py": (
                    "from pydantic import BaseModel\n\n\nclass Dto(BaseModel): ...\n"
                ),
                "app/domain/money.py": "class Money: ...\n",
            },
        )

        assert violations(root, ONLY_SMT203) == []
