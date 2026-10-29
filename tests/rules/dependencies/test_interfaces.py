from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


CONFIG = """
version: 1
project:
  root_packages: [app]
structure:
  contracts:
    orders:
      match: app.orders
      public: [api]
"""
OPTIONS = CheckOptions(select=("SMT107",))


def _project(tmp_path: Path, client: str, *, config: str = CONFIG) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "app/client.py": client,
            "app/orders/__init__.py": "",
            "app/orders/api.py": "from .storage import OrderStore as OrderStore\n",
            "app/orders/storage.py": "class OrderStore: pass\n",
        },
    )


@pytest.mark.parametrize(
    "statement",
    [
        "import app.orders.storage",
        "import app.orders.storage as storage",
        "from app.orders.storage import OrderStore",
        "from app.orders import storage",
        "from .orders import storage as db",
        "from app.orders.storage import *",
    ],
)
def test_direct_private_import_forms_are_rejected(
    tmp_path: Path, statement: str
) -> None:
    _project(tmp_path, statement)

    found = violations(tmp_path, OPTIONS)

    assert len(found) == 1
    assert found[0].target_module == "app.orders.storage"
    assert found[0].line == 1
    assert found[0].expected is not None
    assert found[0].expected["public"] == ["app.orders.api"]


@pytest.mark.parametrize(
    "statement",
    [
        "import app.orders.api",
        "from app.orders import api",
        "from app.orders.api import OrderStore",
        "from app.orders.api import OrderStore as Store",
        "from app.orders.api import *",
    ],
)
@pytest.mark.parametrize("transitive", [True, False])
def test_public_facade_symbol_reexports_are_allowed(
    tmp_path: Path, statement: str, transitive: bool
) -> None:
    config = (
        CONFIG
        + f"\narchitecture:\n  imports: {{transitive: {str(transitive).lower()}}}\n"
    )
    _project(tmp_path, statement, config=config)

    assert violations(tmp_path, OPTIONS) == []


def test_package_can_use_its_own_private_modules(tmp_path: Path) -> None:
    _project(tmp_path, "")
    write_project(
        tmp_path, {"app/orders/service.py": "from .storage import OrderStore\n"}
    )

    assert violations(tmp_path, OPTIONS) == []


def test_explicit_module_alias_through_facade_is_still_private(tmp_path: Path) -> None:
    _project(tmp_path, "from app.orders.api import database\n")
    write_project(
        tmp_path, {"app/orders/api.py": "from . import storage as database\n"}
    )

    assert [v.target_module for v in violations(tmp_path, OPTIONS)] == [
        "app.orders.storage"
    ]


def test_namespace_module_alias_through_facade_is_still_private(tmp_path: Path) -> None:
    _project(tmp_path, "from app.orders.api import database\n")
    write_project(
        tmp_path,
        {
            "app/orders/api.py": "from . import internal as database\n",
            "app/orders/internal/storage.py": "",
        },
    )

    assert [v.target_module for v in violations(tmp_path, OPTIONS)] == [
        "app.orders.internal"
    ]


def test_public_root_facade_does_not_expose_all_descendants(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from app.orders import Store\nfrom app.orders.storage import OrderStore",
        config=CONFIG.replace("[api]", "['.']"),
    )
    write_project(
        tmp_path,
        {"app/orders/__init__.py": "from .storage import OrderStore as Store\n"},
    )

    assert [(v.line, v.target_module) for v in violations(tmp_path, OPTIONS)] == [
        (2, "app.orders.storage")
    ]


@pytest.mark.parametrize(("public", "count"), [("[api]", 1), ("[api.**]", 0)])
def test_subtree_publicity_is_explicit(tmp_path: Path, public: str, count: int) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace("[api]", public),
            "app/orders/api/__init__.py": "",
            "app/orders/api/internal.py": "",
            "app/client.py": "from app.orders.api import internal\n",
        },
    )

    assert len(violations(tmp_path, OPTIONS)) == count


def test_empty_public_interface_closes_package_to_outside_imports(
    tmp_path: Path,
) -> None:
    _project(tmp_path, "import app.orders", config=CONFIG.replace("[api]", "[]"))

    assert [v.target_module for v in violations(tmp_path, OPTIONS)] == ["app.orders"]


def test_omitted_public_interface_does_not_restrict_imports(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from app.orders.storage import OrderStore",
        config=CONFIG.replace("public: [api]", "required: [api]"),
    )

    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(
    "role", ["composition_root: [app.client]", "wiring: [app.client]"]
)
def test_wiring_has_no_implicit_private_access(tmp_path: Path, role: str) -> None:
    _project(
        tmp_path,
        "from app.orders.storage import OrderStore",
        config=CONFIG + f"\narchitecture:\n  {role}\n",
    )

    assert len(violations(tmp_path, OPTIONS)) == 1


@pytest.mark.parametrize(("policy", "count"), [("include", 1), ("ignore", 0)])
def test_type_checking_follows_existing_policy(
    tmp_path: Path, policy: str, count: int
) -> None:
    _project(
        tmp_path,
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.orders.storage import OrderStore\n",
        config=CONFIG + f"\narchitecture:\n  imports: {{type_checking: {policy}}}\n",
    )

    assert len(violations(tmp_path, OPTIONS)) == count


def test_layer_boundaries_still_reject_public_but_illegal_import(
    tmp_path: Path,
) -> None:
    config = (
        CONFIG
        + "\narchitecture:\n  layers:\n    domain: {path: domain}\n    implementation: {path: orders}\n"
    )
    _project(tmp_path, "", config=config)
    write_project(
        tmp_path, {"app/domain/service.py": "from app.orders.api import OrderStore\n"}
    )

    found = violations(tmp_path, CheckOptions(select=("SMT101", "SMT107")))

    assert [v.code for v in found] == ["SMT101"]


def test_nested_contracts_are_additive(tmp_path: Path) -> None:
    config = (
        CONFIG.replace("[api]", "[api.**]")
        + """
    inner:
      match: app.orders.api
      public: ['.']
"""
    )
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/orders/api/__init__.py": "",
            "app/orders/api/private.py": "",
            "app/client.py": "import app.orders.api.private\n",
        },
    )

    found = violations(tmp_path, OPTIONS)

    assert len(found) == 1
    assert found[0].expected is not None
    assert found[0].expected["contract"] == "inner"


def test_one_package_pattern_enforces_interfaces_across_sibling_slices(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace("match: app.orders", "match: app.parts.*"),
            "app/parts/one/api.py": "from app.parts.two import api\nfrom app.parts.two.private import Record\n",
            "app/parts/two/api.py": "from .private import Record as Record\n",
            "app/parts/two/private.py": "class Record: pass\n",
        },
    )

    found = violations(tmp_path, OPTIONS)

    assert [(v.source_module, v.target_module) for v in found] == [
        ("app.parts.one.api", "app.parts.two.private")
    ]
