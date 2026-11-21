from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


CONFIG = """
version: 2
project:
  root_packages: [app]
architecture:
  packages:
    orders:
      match: app.orders
      public: [api]
"""
OPTIONS = CheckOptions(select=("ARC007",))


@pytest.mark.parametrize(
    "test_path",
    [
        "tests/test_orders.py",
        "backend/tests/conftest.py",
        "app/orders/tests/test_orders.py",
    ],
)
@pytest.mark.parametrize(
    "statement",
    [
        "import app.orders.storage as db",
        "from app.orders import storage as db",
        "from app.orders.storage import OrderStore as Store",
        "from app.orders.api import database",
    ],
)
def test_tests_obey_the_public_package_interface(
    tmp_path: Path, test_path: str, statement: str
) -> None:
    test_root = test_path.rsplit("/", 1)[0]
    _project(
        tmp_path,
        "",
        config=CONFIG.replace(
            "root_packages: [app]", f"root_packages: [app]\n  test_roots: [{test_root}]"
        ),
    )
    write_project(
        tmp_path,
        {
            test_path: "from app.orders.api import OrderStore\n" + statement + "\n",
            "app/orders/api.py": "from .storage import OrderStore\nfrom . import storage as database\n",
        },
    )

    [finding] = violations(tmp_path, OPTIONS)

    assert (finding.code, finding.path, finding.line) == ("ARC007", test_path, 2)
    assert finding.target_module == "app.orders.storage"
    assert finding.expected is not None
    assert finding.expected["public"] == ["app.orders.api"]


@pytest.mark.parametrize("policy", ["include", "ignore"])
def test_guarded_test_imports_follow_policy_despite_quality_exclusions(
    tmp_path: Path, policy: str
) -> None:
    _project(
        tmp_path,
        "",
        config=CONFIG
        + f"\n  import_graph: {{type_checking: {policy}}}\ntests: {{quality_exclude: ['tests/**']}}\n",
    )
    write_project(
        tmp_path,
        {
            "tests/test_orders.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.orders.storage import OrderStore\n"
        },
    )
    found = violations(tmp_path, OPTIONS)
    assert [(v.code, v.line) for v in found] == (
        [("ARC007", 3)] if policy == "include" else []
    )


def test_deliberate_private_test_import_can_be_suppressed(tmp_path: Path) -> None:
    _project(tmp_path, "")
    write_project(
        tmp_path,
        {
            "tests/test_orders.py": "from app.orders.storage import OrderStore  # smelt: ignore[ARC007] -- integration test of storage adapter\n"
        },
    )
    report = check(tmp_path, OPTIONS).report
    assert report.violations == []
    assert report.suppressed == 1


def test_project_exclusions_still_exempt_test_imports(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "",
        config=CONFIG.replace(
            "root_packages: [app]",
            "root_packages: [app]\n  exclude:\n    - {path: tests/**, reason: external integration suite}",
        ),
    )
    write_project(
        tmp_path,
        {"tests/test_orders.py": "from app.orders.storage import OrderStore\n"},
    )
    assert violations(tmp_path, OPTIONS) == []


def _project(tmp_path: Path, client: str, *, config: str = CONFIG) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": config,
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
    config = CONFIG + f"\n  import_graph: {{transitive: {str(transitive).lower()}}}\n"
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
            "smelt.yml": CONFIG.replace("[api]", public),
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
        config=CONFIG + f"\n  {role}\n",
    )

    assert len(violations(tmp_path, OPTIONS)) == 1


@pytest.mark.parametrize(("policy", "count"), [("include", 1), ("ignore", 0)])
def test_type_checking_follows_existing_policy(
    tmp_path: Path, policy: str, count: int
) -> None:
    _project(
        tmp_path,
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.orders.storage import OrderStore\n",
        config=CONFIG + f"\n  import_graph: {{type_checking: {policy}}}\n",
    )

    assert len(violations(tmp_path, OPTIONS)) == count


def test_layer_boundaries_still_reject_public_but_illegal_import(
    tmp_path: Path,
) -> None:
    config = (
        CONFIG
        + "\n  layers:\n    domain: {path: domain}\n    implementation: {path: orders}\n"
    )
    _project(tmp_path, "", config=config)
    write_project(
        tmp_path, {"app/domain/service.py": "from app.orders.api import OrderStore\n"}
    )

    found = violations(tmp_path, CheckOptions(select=("ARC001", "ARC007")))

    assert [v.code for v in found] == ["ARC001"]


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
            "smelt.yml": config,
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
            "smelt.yml": CONFIG.replace("match: app.orders", "match: app.parts.*"),
            "app/parts/one/api.py": "from app.parts.two import api\nfrom app.parts.two.private import Record\n",
            "app/parts/two/api.py": "from .private import Record as Record\n",
            "app/parts/two/private.py": "class Record: pass\n",
        },
    )

    found = violations(tmp_path, OPTIONS)

    assert [(v.source_module, v.target_module) for v in found] == [
        ("app.parts.one.api", "app.parts.two.private")
    ]


_FACADE_CONFIG = CONFIG.replace("public: [api]", "public: ['.']")
_SAME_NAMED_FACADE = {
    "app/orders/__init__.py": (
        "from .capture import capture\nfrom .storage import OrderStore\n"
    ),
    "app/orders/capture.py": "def capture() -> None: pass\n",
}


@pytest.mark.parametrize(
    "caller", ["app/client.py", "tests/test_client.py", "tests/orders/test_capture.py"]
)
@pytest.mark.parametrize(
    "statement",
    [
        "from app.orders import capture\n",
        "from app.orders import (\n    OrderStore,\n    capture,\n)\n",
    ],
)
def test_facade_symbol_named_like_its_submodule_is_public(
    tmp_path: Path, caller: str, statement: str
) -> None:
    _project(tmp_path, "", config=_FACADE_CONFIG)
    write_project(tmp_path, {**_SAME_NAMED_FACADE, caller: statement})

    assert violations(tmp_path, OPTIONS) == []


def test_relative_facade_symbol_named_like_its_submodule_is_public(
    tmp_path: Path,
) -> None:
    _project(tmp_path, "from .orders import capture\n", config=_FACADE_CONFIG)
    write_project(tmp_path, _SAME_NAMED_FACADE)

    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(
    ("statement", "target"),
    [
        # A plain import loads the module whatever the facade binds.
        ("import app.orders.capture\n", "app.orders.capture"),
        # Without a facade binding, the name is the submodule itself.
        ("from app.orders import storage\n", "app.orders.storage"),
    ],
)
def test_same_named_module_stays_private_without_facade_binding(
    tmp_path: Path, statement: str, target: str
) -> None:
    _project(tmp_path, statement, config=_FACADE_CONFIG)
    write_project(tmp_path, _SAME_NAMED_FACADE)

    assert [v.target_module for v in violations(tmp_path, OPTIONS)] == [target]
