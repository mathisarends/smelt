from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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

    def test_allowed_layer_does_not_hide_a_forbidden_dependency(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/service.py": "from app.domain import user\n",
                "app/domain/user.py": "from app.infra import db\n",
            },
            LAYERED_CONFIG + "  imports: {transitive: true}\n",
        )

        found = violations(root, ONLY_SMT101)

        assert [(v.source_module, v.target_module) for v in found] == [
            ("app.application.service", "app.infra.db"),
            ("app.domain.user", "app.infra.db"),
        ]
        assert [link.line for link in found[0].import_chain] == [1, 1]

    def test_shortest_witness_is_stable_and_cycles_terminate(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/service.py": "from app import z, a, long\n",
                "app/a.py": "from app import z\n\nfrom app.infra import db\n",
                "app/z.py": "from app import a\nfrom app.infra import db\n",
                "app/long.py": "from app import a\n",
            },
            LAYERED_CONFIG + "  imports: {transitive: true}\n",
        )

        [found] = violations(root, ONLY_SMT101)

        assert [link.imported for link in found.import_chain] == [
            "app.a",
            "app.infra.db",
        ]
        assert [link.line for link in found.import_chain] == [1, 3]

    @pytest.mark.parametrize("guarded_hop", ["first", "middle", "last"])
    @pytest.mark.parametrize("type_checking", ["include", "ignore"])
    def test_type_checking_policy_applies_to_every_hop(
        self, tmp_path: Path, guarded_hop: str, type_checking: str
    ) -> None:
        statements = {
            "first": ("app/application/service.py", "from app import helpers\n"),
            "middle": ("app/helpers.py", "from app import bridge\n"),
            "last": ("app/bridge.py", "from app.infra import db\n"),
        }
        files = dict(statements.values())
        path, statement = statements[guarded_hop]
        files[path] = (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    " + statement
        )
        root = _project(
            tmp_path,
            files,
            LAYERED_CONFIG
            + f"  imports: {{transitive: true, type_checking: {type_checking}}}\n",
        )

        found = violations(root, ONLY_SMT101)

        assert len(found) == (1 if type_checking == "include" else 0)
        if found:
            assert [link.line for link in found[0].import_chain] == [
                3 if hop == guarded_hop else 1 for hop in statements
            ]

    def test_direct_violation_does_not_gain_a_duplicate_indirect_report(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            {
                **INDIRECT_FILES,
                "app/application/service.py": "from app import helpers\nfrom app.infra import db\n",
            },
            LAYERED_CONFIG + "  imports: {transitive: true}\n",
        )

        [found] = violations(root, ONLY_SMT101)

        assert found.line == 2
        assert found.import_chain == ()

    def test_allowed_reachable_layers_are_clean(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            {
                "app/application/service.py": "from app import helpers\n",
                "app/helpers.py": "from app.domain import user\n",
            },
            LAYERED_CONFIG + "  imports: {transitive: true}\n",
        )

        assert violations(root, ONLY_SMT101) == []

    def test_long_chains_have_no_reexport_hop_limit(self, tmp_path: Path) -> None:
        bridges = [f"app.bridge{i}" for i in range(12)]
        files = {
            f"{module.replace('.', '/')}.py": f"import {target}\n"
            for module, target in zip(
                bridges, [*bridges[1:], "app.infra.db"], strict=True
            )
        }
        root = _project(
            tmp_path,
            {"app/application/service.py": "import app.bridge0\n", **files},
            LAYERED_CONFIG + "  imports: {transitive: true}\n",
        )

        [found] = violations(root, ONLY_SMT101)

        assert [link.imported for link in found.import_chain] == [
            *bridges,
            "app.infra.db",
        ]
