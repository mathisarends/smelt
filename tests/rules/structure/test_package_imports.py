from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("IMP004",))
EXPORTS = "from .observe import Observe\nfrom .models import Snapshot\n"
CLIENT = (
    "from app.observation import Observe\nfrom app.observation.models import Snapshot\n"
)


@pytest.mark.parametrize(
    "test_path",
    [
        "tests/test_observation.py",
        "backend/tests/conftest.py",
        "app/observation/tests/test_observation.py",
    ],
)
def test_existing_reexports_are_preferred_in_tests(
    tmp_path: Path, test_path: str
) -> None:
    test_root = test_path.rsplit("/", 1)[0]
    root = _project(
        tmp_path,
        client="",
        config=CONFIG.replace(
            "root_packages: [app]", f"root_packages: [app], test_roots: [{test_root}]"
        ),
    )
    write_project(root, {test_path: CLIENT})

    [finding] = violations(root, OPTIONS)

    assert (finding.code, finding.path, finding.line) == ("IMP004", test_path, 2)
    assert finding.expected is not None
    assert finding.expected["replacement"] == "from app.observation import Snapshot"


@pytest.mark.parametrize(
    ("statement", "replacement"),
    [
        ("from ..models import Snapshot as State", "from .. import Snapshot as State"),
        ("from .models import Snapshot", "from .. import Snapshot"),
    ],
)
def test_relative_test_suggestions_reach_the_facade(
    tmp_path: Path, statement: str, replacement: str
) -> None:
    root = _project(
        tmp_path,
        client="",
        exports="from .tests.models import Snapshot\n"
        if statement.startswith("from .models")
        else EXPORTS,
        config=CONFIG.replace(
            "root_packages: [app]",
            "root_packages: [app], test_roots: [app/observation/tests]",
        ),
    )
    write_project(
        root,
        {
            "app/observation/tests/test_observation.py": statement + "\n",
            "app/observation/tests/models.py": "class Snapshot: pass\n",
        },
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["replacement"] == replacement


@pytest.mark.parametrize(
    ("exports", "expected"),
    [
        ("", 0),
        ("from .models import Snapshot\n", 0),
        ("from .models import Snapshot, Result\n", 3),
    ],
)
def test_mixed_deep_import_requires_one_complete_facade(
    tmp_path: Path, exports: str, expected: int
) -> None:
    root = _project(tmp_path, exports=exports, client="")
    statement = "from app.observation.models import Snapshot, Result\n"
    callers = ["app/first.py", "app/second.py", "app/third.py"]
    write_project(
        root,
        {
            "app/observation/models.py": "class Snapshot: pass\nclass Result: pass\n",
            **dict.fromkeys(callers, statement),
        },
    )
    found = violations(root, OPTIONS)
    assert len(found) == expected
    assert {finding.path for finding in found} == (set(callers) if expected else set())


def _project(
    tmp_path: Path,
    *,
    client: str = CLIENT,
    exports: str = EXPORTS,
    config: str = CONFIG,
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/observation/__init__.py": exports,
            "app/observation/observe.py": "class Observe: pass\n",
            "app/observation/models.py": "class Snapshot: pass\nclass Extra: pass\n",
            "app/client.py": client,
        },
    )


def test_suggests_the_existing_common_package_without_changing_source(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    before = (root / "app/client.py").read_bytes()
    [finding] = violations(root, OPTIONS)

    assert finding.code == "IMP004"
    assert finding.severity is Severity.HINT
    assert (finding.path, finding.line, finding.column) == ("app/client.py", 2, 1)
    assert finding.source_module == "app.client"
    assert finding.target_module == "app.observation.models"
    assert finding.expected == {
        "package": "app.observation",
        "names": ["Snapshot"],
        "replacement": "from app.observation import Snapshot",
        "automatic_rewrite": True,
    }
    assert finding.hint is not None
    assert "combine imports" in finding.hint
    assert (root / "app/client.py").read_bytes() == before


@pytest.mark.parametrize(
    ("client", "import_from"),
    [
        ("from app.observation.models import Snapshot as State\n", "app.observation"),
        ("from .observation.models import Snapshot as State\n", ".observation"),
    ],
)
def test_relative_and_local_imports_preserve_aliases(
    tmp_path: Path, client: str, import_from: str
) -> None:
    [finding] = violations(_project(tmp_path, client=client), OPTIONS)
    assert finding.expected is not None
    assert finding.expected["replacement"] == (
        f"from {import_from} import Snapshot as State"
    )


def test_one_finding_covers_all_names_in_a_statement(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        client="from app.observation.models import Snapshot, Extra as Other\n",
        exports="from .models import Snapshot, Extra\n",
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["names"] == ["Snapshot", "Extra"]
    assert finding.expected["replacement"] == (
        "from app.observation import Snapshot, Extra as Other"
    )


@pytest.mark.parametrize(
    "client",
    [
        "from app.observation import Observe, Snapshot\n",
        "from app.observation.models import Snapshot, Extra\n",
        "from app.observation.models import *\n",
        "from app.observation.models import _Snapshot\n",
        "import app.observation.models\n",
        "from third_party.observation.models import Snapshot\n",
    ],
)
def test_valid_or_unresolved_imports_are_preserved(tmp_path: Path, client: str) -> None:
    assert violations(_project(tmp_path, client=client), OPTIONS) == []


@pytest.mark.parametrize(
    "exports",
    [
        "",
        "from .observe import Observe\n",
        "from .observe import Observe as Snapshot\n",
        "if enabled:\n    from .models import Snapshot\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .models import Snapshot\n",
        "try:\n    from .models import Snapshot\nexcept ImportError:\n    pass\n",
        "from .models import Snapshot\nSnapshot = object()\n",
        "from .models import Snapshot\ndel Snapshot\n",
        "from .models import Snapshot\nif enabled:\n    from .observe import Observe as Snapshot\n",
        "from .models import Snapshot\nfrom .observe import *\n",
        "from .models import Snapshot\n__all__ = []\n",
        "from .models import Snapshot\n__all__ = build_exports()\n",
        "from .models import Snapshot\ndef __getattr__(name): ...\n",
    ],
)
def test_missing_private_or_uncertain_exports_are_preserved(
    tmp_path: Path, exports: str
) -> None:
    assert violations(_project(tmp_path, exports=exports), OPTIONS) == []


@pytest.mark.parametrize(
    "exports",
    [
        "from .models import Snapshot as Snapshot\n__all__ = ['Snapshot']\n",
        "from app.observation.models import Snapshot\n__all__ = []\n__all__ += ['Snapshot']\n",
    ],
)
def test_explicit_public_exports_are_supported(tmp_path: Path, exports: str) -> None:
    [finding] = violations(_project(tmp_path, exports=exports), OPTIONS)
    assert finding.code == "IMP004"


def test_internal_imports_do_not_route_through_their_own_initializer(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, client="")
    write_project(root, {"app/observation/consumer.py": CLIENT})
    assert violations(root, OPTIONS) == []


def test_nearest_eligible_ancestor_is_suggested(tmp_path: Path) -> None:
    root = _project(tmp_path, client="from app.observation.deep.models import Snapshot")
    write_project(
        root,
        {
            "app/observation/__init__.py": "from .deep.models import Snapshot\n",
            "app/observation/deep/__init__.py": "from .models import Snapshot\n",
            "app/observation/deep/models.py": "class Snapshot: pass\n",
        },
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["package"] == "app.observation.deep"


def test_non_direct_reexport_chains_are_not_inferred(tmp_path: Path) -> None:
    root = _project(tmp_path, exports="from .api import Snapshot\n")
    write_project(root, {"app/observation/api.py": "from .models import Snapshot\n"})
    found = violations(root, OPTIONS)
    assert not any(finding.path == "app/client.py" for finding in found)


@pytest.mark.parametrize("setting", ["include", "ignore"])
def test_type_checking_policy_is_respected(tmp_path: Path, setting: str) -> None:
    root = _project(
        tmp_path,
        client=(
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    from app.observation.models import Snapshot\n"
        ),
        config=CONFIG
        + f"architecture: {{import_graph: {{type_checking: {setting}}}}}\n",
    )
    found = violations(root, OPTIONS)
    assert [finding.code for finding in found] == (
        ["IMP004"] if setting == "include" else []
    )


def test_rule_is_off_by_default(tmp_path: Path) -> None:
    assert violations(_project(tmp_path)) == []


@pytest.mark.parametrize("name", ["IMP004", "imports/prefer-package-import"])
def test_explicit_settings_enable_the_rule(tmp_path: Path, name: str) -> None:
    [finding] = violations(
        _project(tmp_path, config=CONFIG + f"rules: {{{name}: hint}}")
    )
    assert finding.code == "IMP004"


def test_closest_package_can_disable_the_rule(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        config=CONFIG
        + "rules: {imports/prefer-package-import: error}\n"
        + "style:\n  overrides:\n    local:\n      match: app\n"
        + "      rules: {imports/prefer-package-import: off}\n",
    )
    assert violations(root, OPTIONS) == []


def test_deliberate_implementation_import_can_be_suppressed(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        client=CLIENT.rstrip()
        + "  # smelt: ignore[IMP004] -- avoid initializer side effects here\n",
    )
    outcome = check(root, OPTIONS)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1


SITE = {
    "web/__init__.py": "from .routing import APIRouter as APIRouter\n"
    "from .app import App\n",
    "web/routing.py": "class APIRouter: ...\n",
    "web/app.py": "class App: ...\n",
    "web/orm/__init__.py": "from .session import Session\n",
    "web/orm/session.py": "class Session: ...\nclass Engine: ...\n",
}
THIRD_PARTY = CONFIG + "style: {third_party_reexports: [web]}\n"


def _installed_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: str,
    config: str = THIRD_PARTY,
) -> Path:
    write_project(tmp_path / "site", SITE)
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    return _project(tmp_path / "project", client=client, config=config)


@pytest.mark.parametrize(
    ("statement", "replacement"),
    [
        (
            "from web.routing import APIRouter as Router",
            "from web import APIRouter as Router",
        ),
        ("from web.orm.session import Session", "from web.orm import Session"),
        ("from web.app import App", "from web import App"),
    ],
)
def test_listed_installed_packages_suggest_their_nearest_reexport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    replacement: str,
) -> None:
    root = _installed_project(tmp_path, monkeypatch, client=statement + "\n")

    [finding] = violations(root, OPTIONS)

    assert (finding.code, finding.path, finding.line) == ("IMP004", "app/client.py", 1)
    assert finding.expected is not None
    assert finding.expected["replacement"] == replacement


@pytest.mark.parametrize(
    ("statement", "config"),
    [
        ("from web.routing import APIRouter", CONFIG),
        ("from web.routing import APIRouter", THIRD_PARTY.replace("[web]", "[]")),
        ("from web.orm.session import Engine", THIRD_PARTY),
        ("from web.orm.session import Session, Engine", THIRD_PARTY),
        ("from web import App", THIRD_PARTY),
        ("import web.routing", THIRD_PARTY),
        ("from absent.core import Thing", THIRD_PARTY.replace("[web]", "[absent]")),
    ],
)
def test_unlisted_unexported_or_missing_installed_names_stay_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str, config: str
) -> None:
    root = _installed_project(
        tmp_path, monkeypatch, client=statement + "\n", config=config
    )

    assert violations(root, OPTIONS) == []


def test_package_overrides_scope_the_installed_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = (
        CONFIG
        + "style:\n  overrides:\n    api: {match: app.api, third_party_reexports: [web]}\n"
    )
    root = _installed_project(
        tmp_path,
        monkeypatch,
        client="from web.routing import APIRouter\n",
        config=config,
    )
    write_project(
        root,
        {
            "app/api/__init__.py": "",
            "app/api/routes.py": "from web.routing import APIRouter\n",
        },
    )

    assert [finding.path for finding in violations(root, OPTIONS)] == [
        "app/api/routes.py"
    ]


def test_project_packages_shadow_a_listed_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_project(
        tmp_path,
        monkeypatch,
        client="from lib.vendor.tools import Tool\n",
        config=THIRD_PARTY.replace("[web]", "[lib]").replace(
            "root_packages: [app]", "root_packages: [app, lib]"
        ),
    )
    write_project(root, {"lib/__init__.py": ""})
    write_project(
        tmp_path / "site", {"lib/__init__.py": "from .vendor.tools import Tool\n"}
    )

    assert violations(root, OPTIONS) == []
