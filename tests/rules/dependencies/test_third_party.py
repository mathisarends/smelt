from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

ONLY_SMT103 = CheckOptions(select=("SMT103",))


def _project(
    tmp_path: Path, third_party: str, source: str, *, transitive: bool = False
) -> Path:
    config = f"""
version: 1
project:
  root_packages: [app]
architecture:
  imports: {{transitive: {str(transitive).lower()}}}
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

    @pytest.mark.parametrize(
        "policy",
        [
            "{default: deny, allow: [sqlalchemy.orm]}",
            "{default: allow, deny: [sqlalchemy.sql]}",
        ],
    )
    def test_all_aliases_of_a_squashed_external_import_are_checked(
        self, tmp_path: Path, policy: str
    ) -> None:
        root = _project(tmp_path, policy, "import sqlalchemy.orm, sqlalchemy.sql\n")

        [found] = violations(root, ONLY_SMT103)

        assert found.target_module == "sqlalchemy"
        assert found.column == 1

    @pytest.mark.parametrize(
        "source",
        [
            "from requests import Session; from pydantic import BaseModel\n",
            "from pydantic import BaseModel; from requests import Session\n",
            "import requests; from pydantic import BaseModel\n",
            "from sqlalchemy import text; from sqlalchemy.orm import Mapped\n",
        ],
    )
    def test_imports_on_the_same_line_keep_their_own_names(
        self, tmp_path: Path, source: str
    ) -> None:
        root = _project(tmp_path, self.policy, source)

        [found] = violations(root, ONLY_SMT103)

        assert found.target_module == (
            "sqlalchemy" if "sqlalchemy" in source else "requests"
        )
        assert (
            found.column
            == source.index(
                "text"
                if "sqlalchemy" in source
                else ("Session" if "Session" in source else "requests")
            )
            + 1
        )

    def test_repeated_imports_on_the_same_line_count_individually(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            self.policy,
            "from requests import Session; from requests import get\n",
        )

        found = violations(root, ONLY_SMT103)

        assert len(found) == 2
        assert found[0].column != found[1].column

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


class TestTransitive:
    @pytest.mark.parametrize("transitive", [False, True])
    def test_framework_import_through_shared_code(
        self, tmp_path: Path, transitive: bool
    ) -> None:
        root = _project(
            tmp_path,
            "{default: allow, deny: [sqlalchemy]}",
            "\nfrom app.shared import utils\n",
            transitive=transitive,
        )
        write_project(
            root,
            {
                "app/shared/utils.py": "import os\n\nfrom sqlalchemy.orm import Session\n",
            },
        )

        found = violations(root, ONLY_SMT103)

        assert len(found) == int(transitive)
        if found:
            violation = found[0]
            assert (violation.path, violation.line, violation.target_module) == (
                "app/domain/model.py",
                1,
                "sqlalchemy",
            )
            assert violation.edge == "domain -> sqlalchemy"
            assert [link.to_json() for link in violation.import_chain] == [
                {
                    "importer": "app.domain.model",
                    "imported": "app.shared.utils",
                    "line": 1,
                },
                {"importer": "app.shared.utils", "imported": "sqlalchemy", "line": 3},
            ]
            assert "indirectly" in violation.message
            assert violation.expected == {
                "third_party": {"default": "allow", "deny": ["sqlalchemy"]}
            }

    @pytest.mark.parametrize(
        "statement",
        [
            "from sqlalchemy.orm import Session\nfrom sqlalchemy.sql import text\n",
            "from sqlalchemy.sql import text\nfrom sqlalchemy.orm import Session\n",
            "import sqlalchemy.orm, sqlalchemy.sql\n",
        ],
    )
    def test_external_submodule_policy_checks_every_reachable_statement(
        self, tmp_path: Path, statement: str
    ) -> None:
        root = _project(
            tmp_path,
            "{default: deny, allow: [sqlalchemy.orm]}",
            "from app import bridge\n",
            transitive=True,
        )
        write_project(root, {"app/bridge.py": statement})

        [found] = violations(root, ONLY_SMT103)

        assert found.target_module == "sqlalchemy"
        assert found.import_chain[-1].importer == "app.bridge"

    def test_allowed_submodule_does_not_hide_denied_import_on_another_route(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path,
            "{default: deny, allow: [sqlalchemy.orm]}",
            "from sqlalchemy.orm import Session\nfrom app import a, b\n",
            transitive=True,
        )
        write_project(
            root,
            {
                "app/a.py": "from sqlalchemy.orm import Session\n",
                "app/b.py": "from sqlalchemy.sql import text\n",
            },
        )

        [found] = violations(root, ONLY_SMT103)

        assert [link.imported for link in found.import_chain] == ["app.b", "sqlalchemy"]

    def test_allowed_dependencies_and_stdlib_remain_clean(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path,
            "{default: deny, allow: [sqlalchemy.orm]}",
            "from app import bridge\n",
            transitive=True,
        )
        write_project(
            root, {"app/bridge.py": "import os\nfrom sqlalchemy.orm import Session\n"}
        )

        assert violations(root, ONLY_SMT103) == []

    def test_one_shortest_report_per_denied_package(self, tmp_path: Path) -> None:
        root = _project(tmp_path, "deny", "from app import a, b\n", transitive=True)
        write_project(
            root,
            {
                "app/a.py": "import requests\nfrom requests import get\n",
                "app/b.py": "from requests import Session\n",
            },
        )

        [found] = violations(root, ONLY_SMT103)

        assert [link.imported for link in found.import_chain] == ["app.a", "requests"]

    @pytest.mark.parametrize("guarded_hop", ["first", "last"])
    @pytest.mark.parametrize("type_checking", ["include", "ignore"])
    def test_type_checking_imports_cannot_bridge_an_ignored_chain(
        self, tmp_path: Path, guarded_hop: str, type_checking: str
    ) -> None:
        root = _project(tmp_path, "deny", "from app import bridge\n", transitive=True)
        write_project(root, {"app/bridge.py": "import requests\n"})
        config_path = root / "smelt.yaml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "transitive: true", f"transitive: true, type_checking: {type_checking}"
            ),
            encoding="utf-8",
        )
        path = root / (
            "app/domain/model.py" if guarded_hop == "first" else "app/bridge.py"
        )
        path.write_text(
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    "
            + path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        assert len(violations(root, ONLY_SMT103)) == int(type_checking == "include")
