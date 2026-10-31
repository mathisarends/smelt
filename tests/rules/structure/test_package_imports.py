from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT319",))
EXPORTS = "from .observe import Observe\nfrom .models import Snapshot\n"
CLIENT = (
    "from app.observation import Observe\nfrom app.observation.models import Snapshot\n"
)


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
            "smelt.yaml": config,
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

    assert finding.code == "SMT319"
    assert finding.severity is Severity.HINT
    assert (finding.path, finding.line, finding.column) == ("app/client.py", 2, 1)
    assert finding.source_module == "app.client"
    assert finding.target_module == "app.observation.models"
    assert finding.expected == {
        "package": "app.observation",
        "names": ["Snapshot"],
        "replacement": "from app.observation import Snapshot",
        "automatic_rewrite": False,
    }
    assert finding.hint is not None
    assert "combine imports" in finding.hint
    assert (root / "app/client.py").read_bytes() == before


@pytest.mark.parametrize(
    ("client", "import_from"),
    [
        ("from app.observation.models import Snapshot as State\n", "app.observation"),
        ("from .observation.models import Snapshot as State\n", ".observation"),
        (
            "def run():\n    from app.observation.models import Snapshot as State\n",
            "app.observation",
        ),
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
        "from .models import Snapshot as State\n",
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
    assert finding.code == "SMT319"


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
        config=CONFIG + f"architecture: {{imports: {{type_checking: {setting}}}}}\n",
    )
    found = violations(root, OPTIONS)
    assert [finding.code for finding in found] == (
        ["SMT319"] if setting == "include" else []
    )


def test_rule_is_off_by_default(tmp_path: Path) -> None:
    assert violations(_project(tmp_path)) == []


@pytest.mark.parametrize("name", ["SMT319", "prefer-package-import"])
def test_explicit_settings_enable_the_rule(tmp_path: Path, name: str) -> None:
    [finding] = violations(
        _project(tmp_path, config=CONFIG + f"rules: {{{name}: hint}}")
    )
    assert finding.code == "SMT319"


def test_closest_package_can_disable_the_rule(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        config=CONFIG
        + "rules: {prefer-package-import: error}\n"
        + "structure:\n  contracts:\n    local:\n      match: app\n"
        + "      rules: {prefer-package-import: off}\n",
    )
    assert violations(root, OPTIONS) == []


def test_deliberate_implementation_import_can_be_suppressed(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        client=CLIENT.rstrip()
        + "  # smelt: ignore[SMT319] -- avoid initializer side effects here\n",
    )
    outcome = check(root, OPTIONS)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1


def test_cached_findings_follow_export_edits_and_deletion(tmp_path: Path) -> None:
    root = _project(tmp_path, config=CONFIG + "rules: {prefer-package-import: hint}\n")
    [initial] = violations(root)
    assert initial.code == "SMT319"
    initializer = root / "app/observation/__init__.py"
    for exports in ("", EXPORTS):
        initializer.write_text(exports, encoding="utf-8")
        warm = check(root).report.violations
        fresh = check(root, CheckOptions(use_cache=False)).report.violations
        assert warm == fresh
        assert [finding.code for finding in warm] == (["SMT319"] if exports else [])
    initializer.unlink()
    assert violations(root) == []
    assert violations(root, CheckOptions(use_cache=False)) == []
