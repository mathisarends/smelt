from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app, other]}\n"
SELECT = CheckOptions(select=("SMT110",))
IMPORTS = "from app.users.read import Read\nfrom app.users.write import Write\nfrom other.pay import Pay\n"


def test_counts_across_packages_and_groups_evidence(tmp_path: Path) -> None:
    root = _project(tmp_path, IMPORTS)
    [finding] = violations(root)
    assert (finding.code, finding.path, finding.line) == ("SMT110", "app/client.py", 1)
    assert finding.severity == Severity.HINT
    assert finding.expected == {
        "max_module_imports": 2,
        "count": 3,
        "modules": ["app.users.read", "app.users.write", "other.pay"],
        "imports_by_package": {
            "app.users": ["app.users.read", "app.users.write"],
            "other": ["other.pay"],
        },
        "import_edges": [
            "app.client -> app.users.read",
            "app.client -> app.users.write",
            "app.client -> other.pay",
        ],
        "automatic_rewrite": False,
    }
    assert "SMT108" in (finding.hint or "")
    assert (root / "app/client.py").read_text() == IMPORTS


def test_duplicate_symbols_aliases_and_external_imports_do_not_inflate_count(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "import os\nimport pydantic\nfrom app.users.read import Read, Extra\nfrom app.users.read import Read as Again\nimport app.users.read as reads\nfrom app.users.write import Write\n",
    )
    assert violations(root, SELECT) == []


@pytest.mark.parametrize(
    "caller",
    [
        "from app.users import read, write\nfrom other import pay\n",
        "from .users import read, write\nfrom other.pay import Pay\n",
        "import app.users.read, app.users.write\nimport other.pay\n",
    ],
)
def test_grouped_and_relative_module_imports_count_targets(
    tmp_path: Path, caller: str
) -> None:
    [finding] = violations(_project(tmp_path, caller), SELECT)
    assert finding.expected is not None
    assert finding.expected["count"] == 3
    assert finding.expected["modules"] == [
        "app.users.read",
        "app.users.write",
        "other.pay",
    ]


def test_reexports_count_at_the_facade_used_by_the_caller(tmp_path: Path) -> None:
    root = _project(
        tmp_path, "from app.users import Read, Write\nfrom other.pay import Pay\n"
    )
    assert violations(root, SELECT) == []
    (root / "smelt.yml").write_text(
        CONFIG + "style: {max_module_imports: 1}\n", encoding="utf-8"
    )
    [finding] = violations(root, SELECT)
    assert finding.expected is not None
    assert finding.expected["modules"] == ["app.users", "other.pay"]


@pytest.mark.parametrize(("policy", "count"), [("include", 1), ("ignore", 0)])
def test_type_checking_imports_follow_existing_policy(
    tmp_path: Path, policy: str, count: int
) -> None:
    root = _project(
        tmp_path,
        "from app.users.read import Read\nfrom app.users.write import Write\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from other.pay import Pay\n",
        settings=f"style: {{max_module_imports: 2}}\narchitecture:\n  import_graph: {{type_checking: {policy}}}\n",
    )
    assert len(violations(root, SELECT)) == count


@pytest.mark.parametrize("exempt", [True, False])
def test_initializers_and_composition_roots_have_configurable_exemptions(
    tmp_path: Path, exempt: bool
) -> None:
    settings = "style:\n  max_module_imports: 2\n"
    if not exempt:
        settings += "  module_import_exemptions: []\n"
    settings += "architecture: {composition_root: [app.bootstrap]}\n"
    root = _project(tmp_path, "", settings=settings)
    write_project(root, {"app/__init__.py": IMPORTS, "app/bootstrap.py": IMPORTS})
    found = violations(root, SELECT)
    assert [finding.path for finding in found] == (
        [] if exempt else ["app/__init__.py", "app/bootstrap.py"]
    )


def test_closest_package_threshold_and_empty_exemption_override(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        IMPORTS,
        settings="""\
style:
  max_module_imports: 3
  overrides:
    outer: {match: app, max_module_imports: 2}
    inner: {match: app.loose, max_module_imports: 3}
    facade: {match: app.loose, module_import_exemptions: []}
""",
    )
    write_project(
        root, {"app/loose/__init__.py": IMPORTS, "app/loose/client.py": IMPORTS}
    )
    [finding] = violations(root, SELECT)
    assert finding.path == "app/client.py"
    # The empty exemption override inherits the threshold independently.
    (root / "app/loose/__init__.py").write_text(
        IMPORTS + "import app.client\n", encoding="utf-8"
    )
    assert [finding.path for finding in violations(root, SELECT)] == [
        "app/client.py",
        "app/loose/__init__.py",
    ]


def test_contract_threshold_activates_only_the_configured_scope(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        IMPORTS,
        settings="style:\n  overrides:\n    users: {match: app.users, max_module_imports: 0}\n",
    )
    write_project(root, {"app/users/client.py": "from other.pay import Pay\n"})
    [finding] = violations(root)
    assert finding.path == "app/users/client.py"


def test_rule_selection_requires_a_limit_and_zero_is_supported(tmp_path: Path) -> None:
    root = _project(tmp_path, IMPORTS, settings="")
    assert violations(root, SELECT) == []
    (root / "smelt.yml").write_text(
        CONFIG + "style: {max_module_imports: 0}\n", encoding="utf-8"
    )
    [finding] = violations(root)
    assert finding.expected is not None
    assert finding.expected["max_module_imports"] == 0
    assert violations(root, CheckOptions(ignore=("SMT110",))) == []


def test_off_override_suppression_and_tests_are_respected(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        IMPORTS,
        settings="""\
style:
  max_module_imports: 2
  overrides:
    quiet: {match: app.quiet, rules: {imports/excess-module-imports: off}}
rules: {imports/excess-module-imports: warning}
""",
    )
    write_project(
        root,
        {
            "app/quiet/__init__.py": "",
            "app/quiet/client.py": IMPORTS,
            "app/intentional.py": IMPORTS.replace(
                "from app.users.read import Read",
                "from app.users.read import Read  # smelt: ignore[SMT110] -- deliberate coordinator",
            ),
            "tests/test_client.py": IMPORTS,
        },
    )
    [finding] = violations(root)
    assert (finding.path, finding.severity) == ("app/client.py", Severity.WARNING)
    assert violations(root, CheckOptions(use_cache=False)) == violations(root)


def test_cache_tracks_reexport_inventory_changes(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from app.users import read_api, write_api\nfrom other.pay import Pay\n",
    )
    write_project(
        root,
        {
            "app/users/__init__.py": "from .read import Read as read_api\nfrom .write import Write as write_api\n",
        },
    )
    assert violations(root, SELECT) == []
    # Replacing exported values with submodules changes the target inventory.
    write_project(
        root,
        {
            "app/users/__init__.py": "",
            "app/users/read_api.py": "",
            "app/users/write_api.py": "",
        },
    )
    [finding] = violations(root, SELECT)
    assert finding.expected is not None
    assert finding.expected["modules"] == [
        "app.users.read_api",
        "app.users.write_api",
        "other.pay",
    ]
    assert violations(root, CheckOptions(select=("SMT110",), use_cache=False)) == [
        finding
    ]


@pytest.mark.parametrize(
    "settings",
    [
        "style: {max_module_imports: -1}",
        "style: {overrides: {bad: {match: app, max_module_imports: -1}}}",
        "style: {module_import_exemptions: [unknown]}",
    ],
)
def test_rejects_invalid_policy(tmp_path: Path, settings: str) -> None:
    with pytest.raises(ConfigError):
        violations(_project(tmp_path, "", settings=settings), SELECT)


def _project(
    tmp_path: Path,
    caller: str,
    *,
    settings: str = "style: {max_module_imports: 2}\n",
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + settings,
            "app/__init__.py": "",
            "app/client.py": caller,
            "app/users/__init__.py": "from .read import Read as Read\nfrom .write import Write as Write\n",
            "app/users/read.py": "Read = 1\nExtra = 2\n",
            "app/users/write.py": "Write = 1\n",
            "other/__init__.py": "",
            "other/pay.py": "Pay = 1\n",
        },
    )
