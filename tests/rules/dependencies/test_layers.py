from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

ONLY_SMT101 = CheckOptions(select=("SMT101",))


def _project(
    tmp_path: Path, files: dict[str, str], config: str = LAYERED_CONFIG
) -> Path:
    base = {
        "smelt.yaml": config,
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/user.py": "class User: ...\n",
        "app/application/__init__.py": "",
        "app/infra/__init__.py": "",
        "app/infra/db.py": "class Db: ...\n",
    }
    return write_project(tmp_path, {**base, **files})


class TestDirectImports:
    def test_allowed_dependency_is_clean(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {"app/application/service.py": "from app.domain.user import User\n"},
        )

        assert violations(root, ONLY_SMT101) == []

    def test_forbidden_dependency_reports_import_line(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/service.py": "import os\n\nfrom app.infra.db import Db\n"
            },
        )

        [found] = violations(root, ONLY_SMT101)

        assert (found.path, found.line, found.column, found.end_column) == (
            "app/application/service.py",
            3,
            26,
            28,
        )
        assert found.message == "application must not depend on infrastructure"
        assert found.expected == {"may_depend_on": ["domain"]}
        assert found.target_module == "app.infra.db"

    def test_relative_imports_are_resolved(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"app/domain/rules.py": "from ..infra import db\n"})

        [found] = violations(root, ONLY_SMT101)

        assert (found.layer, found.target_module) == ("domain", "app.infra.db")

    def test_imports_inside_the_same_layer_are_allowed(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path, {"app/domain/order.py": "from app.domain.user import User\n"}
        )

        assert violations(root, ONLY_SMT101) == []


class TestTypeChecking:
    source = """
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from app.infra.db import Db
    """

    def test_type_checking_imports_count_by_default(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"app/application/service.py": self.source})

        assert [v.line for v in violations(root, ONLY_SMT101)] == [4]

    def test_type_checking_imports_can_be_ignored(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + "  imports:\n    type_checking: ignore\n"
        root = _project(tmp_path, {"app/application/service.py": self.source}, config)

        assert violations(root, ONLY_SMT101) == []


INDIRECT_FILES = {
    "app/helpers.py": "from app.infra.db import Db\n",
    "app/application/service.py": "from app import helpers\n",
}


class TestTransitive:
    def test_indirect_chain_is_ignored_by_default(self, tmp_path: Path) -> None:
        root = _project(tmp_path, INDIRECT_FILES)

        assert violations(root, ONLY_SMT101) == []

    def test_indirect_chain_reports_full_path(self, tmp_path: Path) -> None:
        config = LAYERED_CONFIG + "  imports:\n    transitive: true\n"
        root = _project(tmp_path, INDIRECT_FILES, config)

        [found] = violations(root, ONLY_SMT101)

        assert (
            found.message
            == "application must not depend on infrastructure (indirectly)"
        )
        assert [(link.importer, link.imported) for link in found.import_chain] == [
            ("app.application.service", "app.helpers"),
            ("app.helpers", "app.infra.db"),
        ]
