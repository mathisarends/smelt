from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT339",))


def _project(tmp_path: Path, client: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/service.py": (
                "def _helper(): pass\nclass _Service: pass\n"
                "def public(): pass\n__version__ = '1'\n_secret = 1\n"
            ),
            "app/client.py": client,
        },
    )


@pytest.mark.parametrize(
    ("client", "target", "access"),
    [
        ("from .service import _helper", "app.service._helper", "import"),
        ("from app.service import _helper as shared", "app.service._helper", "import"),
        ("from .service import _Service", "app.service._Service", "import"),
        ("from .service import _secret", "app.service._secret", "import"),
        (
            "def run():\n    from .service import _helper",
            "app.service._helper",
            "import",
        ),
        ("import app.service\napp.service._helper()", "app.service._helper", "read"),
        ("import app.service as s\ns._helper()", "app.service._helper", "read"),
        ("from . import service as s\ns._helper()", "app.service._helper", "read"),
        (
            "def run():\n    import app.service as s\n    s._helper()",
            "app.service._helper",
            "read",
        ),
        (
            "import app.service as s\ndef run():\n    s._helper()",
            "app.service._helper",
            "read",
        ),
        (
            "import app.service as s\nclass Client:\n    def run(self): s._helper()",
            "app.service._helper",
            "read",
        ),
        (
            "import app.service as s\ndef run():\n    def inner(): s._helper()",
            "app.service._helper",
            "read",
        ),
        (
            "import app.service as s\nf = lambda: s._helper()",
            "app.service._helper",
            "read",
        ),
        (
            "import app.service as s\nx = [s._helper() for item in items]",
            "app.service._helper",
            "read",
        ),
        (
            "import app.service as s\nclass Client:\n    s = object()\n    def run(self): s._helper()",
            "app.service._helper",
            "read",
        ),
    ],
)
def test_reports_project_private_imports_and_qualified_reads(
    tmp_path: Path, client: str, target: str, access: str
) -> None:
    root = _project(tmp_path, client)
    before = (root / "app/client.py").read_bytes()
    [finding] = violations(root, OPTIONS)
    assert finding.code == "SMT339"
    assert finding.severity is Severity.WARNING
    assert finding.source_module == "app.client"
    assert finding.target_module == "app.service"
    assert finding.path == "app/client.py"
    assert finding.expected == {
        "private_target": target,
        "defined_in": target,
        "access": access,
        "automatic_rewrite": False,
    }
    assert (root / "app/client.py").read_bytes() == before


@pytest.mark.parametrize(
    "client",
    [
        "from .service import public",
        "from .service import __version__",
        "from third_party.service import _helper",
        "from .service import _unknown",
        "from .service import *",
        "import app.service as s\ns.__version__",
        "import app.service as s\ns._unknown()",
        "import app.service as s\ns.public._member()",
        "from .service import public\npublic._member()",
        "def _helper(): pass\n_helper()",
        "_helper = 1\nfrom app.client import _helper",
        "import app.client as c\n_helper = 1\nc._helper",
        "import app.service as s\ns = unknown\ns._helper()",
        "import app.service as s\ndef run(s): s._helper()",
        "import app.service as s\nasync def run(*, s): s._helper()",
        "import app.service as s\ndef run():\n    s._helper()\n    s = fake",
        "import app.service as s\ndef run():\n    import third_party as s\n    s._helper()",
        "import app.service as s\nf = lambda s: s._helper()",
        "import app.service as s\nx = [s._helper() for s in items]",
        "import app.service as s\nx = {s._helper() for s in items}",
        "import app.service as s\nx = {s._helper(): 1 for s in items}",
        "import app.service as s\nx = (s._helper() for s in items)",
        "import app.service as s\nclass Client:\n    s = fake\n    result = s._helper()",
        "import app.service as s\ndef run():\n    global s\n    s._helper()",
        "import app.service as s\nfrom somewhere import *\ns._helper()",
        "import app.service as s\ntry:\n    pass\nexcept Exception as s:\n    s._helper()",
        "import app.service as s\nmatch value:\n    case s: s._helper()",
        "import app.service as s\nfor s in items:\n    s._helper()",
        "import app.service as s\nwith resource() as s:\n    s._helper()",
        "import app.service as s\ns._secret = 2",
    ],
)
def test_excludes_public_unknown_and_shadowed_receivers(
    tmp_path: Path, client: str
) -> None:
    assert violations(_project(tmp_path, client), OPTIONS) == []


def test_import_locations_and_multiline_suppression_are_alias_specific(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "from .service import (\n"
        "    _helper as helper,  # smelt: ignore[SMT339] -- Compatibility adapter.\n"
        "    _Service,\n)\n",
    )
    [finding] = violations(root, OPTIONS)
    assert (finding.line, finding.column) == (3, 5)
    assert finding.expected is not None
    assert finding.expected["private_target"] == "app.service._Service"


@pytest.mark.parametrize("policy", ["include", "ignore"])
def test_type_checking_policy_applies_to_imports_and_reads(
    tmp_path: Path, policy: str
) -> None:
    root = _project(
        tmp_path,
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .service import _Service\n"
        "    import app.service as s\n"
        "    s._helper()\n"
        "s._helper()\n"
        "import app.service as runtime\n"
        "if TYPE_CHECKING:\n"
        "    runtime._helper()\n",
        config=CONFIG + f"architecture:\n  imports: {{type_checking: {policy}}}\n",
    )
    found = violations(root, OPTIONS)
    assert [v.line for v in found] == ([3, 5, 6, 9] if policy == "include" else [])


@pytest.mark.parametrize(
    ("initializer", "flagged"),
    [
        ("from .service import _helper as helper", False),
        ("from .service import _helper as _alias", True),
        ("from .service import _helper", True),
        ("from ._impl import _helper as helper", False),
        ("if enabled:\n    from .service import _helper as helper", False),
        ("from other.service import _helper as helper", True),
        ("def run():\n    from .service import _helper as helper", True),
    ],
)
def test_only_own_initializer_public_reexports_are_exempt(
    tmp_path: Path, initializer: str, flagged: bool
) -> None:
    root = _project(tmp_path, "")
    write_project(
        root,
        {
            "smelt.yml": CONFIG.replace("[app]", "[app, other]"),
            "app/__init__.py": initializer,
            "app/_impl.py": "def _helper(): pass\n",
            "other/__init__.py": "",
            "other/service.py": "def _helper(): pass\n",
        },
    )
    assert bool(violations(root, OPTIONS)) is flagged


def test_public_reexport_stays_public_and_private_module_names_are_separate(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "from . import helper\nfrom . import _impl\nimport app._impl\n"
        "app._impl._helper()\n",
    )
    write_project(
        root,
        {
            "app/__init__.py": "from ._impl import _helper as helper",
            "app/_impl.py": "def _helper(): pass\n",
        },
    )
    [finding] = violations(root, OPTIONS)
    assert finding.line == 4
    assert finding.expected is not None
    assert finding.expected["private_target"] == "app._impl._helper"


def test_test_files_remain_exclusively_with_smt420(tmp_path: Path) -> None:
    root = _project(tmp_path, "")
    write_project(
        root,
        {"tests/test_service.py": "from app.service import _helper\n"},
    )
    found = violations(root, CheckOptions(select=("SMT339", "SMT420")))
    assert [(v.code, v.path) for v in found] == [("SMT420", "tests/test_service.py")]


def test_rule_is_explicit_and_supports_package_overrides(tmp_path: Path) -> None:
    root = _project(tmp_path, "from .service import _helper")
    assert violations(root) == []
    [finding] = violations(root, CheckOptions(select=("private-symbol-import",)))
    assert finding.severity is Severity.WARNING
    assert [v.code for v in violations(root, CheckOptions(select=("SMT33",)))] == [
        "SMT339"
    ]
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + "rules: {private-symbol-import: error}\n"
            + "structure:\n  contracts:\n    caller:\n      match: app\n"
            + "      rules: {private-symbol-import: off}\n"
        },
    )
    assert violations(root, OPTIONS) == []
    write_project(
        root,
        {"smelt.yml": CONFIG + "rules: {private-symbol-import: error}\n"},
    )
    [finding] = violations(root)
    assert finding.severity is Severity.ERROR


def test_cross_module_definition_changes_invalidate_cached_decisions(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "import app.service as s\ns._helper()")
    assert len(violations(root, OPTIONS)) == 1
    for definitions in ("", "from third_party import _helper\n", "_helper = 1\n"):
        write_project(root, {"app/service.py": definitions})
        cached = violations(root, OPTIONS)
        fresh = violations(root, CheckOptions(select=("SMT339",), use_cache=False))
        assert cached == fresh
        assert len(cached) == (1 if definitions == "_helper = 1\n" else 0)


@pytest.mark.parametrize("with_init", [True, False])
def test_nested_relative_imports_and_namespace_source_layouts(
    tmp_path: Path, with_init: bool
) -> None:
    files = {
        "smelt.yml": "version: 1\nproject: {root_packages: [app], source_roots: [src]}\n",
        "src/app/feature/client.py": "from ..service import _helper\n",
        "src/app/service.py": "def _helper(): pass\n",
    }
    if with_init:
        files.update({"src/app/__init__.py": "", "src/app/feature/__init__.py": ""})
    root = write_project(tmp_path, files)
    [finding] = violations(root, OPTIONS)
    assert finding.path == "src/app/feature/client.py"
    assert finding.expected is not None
    assert finding.expected["private_target"] == "app.service._helper"


def test_reexported_module_aliases_and_private_symbol_chains(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from .bridge import service as s\ns._helper()\n"
        "from .bridge import _helper\nfrom .bridge import _external\n",
    )
    write_project(
        root,
        {
            "app/bridge.py": "from . import service\nfrom .service import _helper\n"
            "from third_party import _external\n"
        },
    )
    found = [v for v in violations(root, OPTIONS) if v.path == "app/client.py"]
    assert [v.line for v in found] == [2, 3]
    assert all(
        v.expected and v.expected["defined_in"] == "app.service._helper" for v in found
    )
