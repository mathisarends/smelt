from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_policy_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("VIS005",))


def _symbol_project(tmp_path: Path, client: str, *, config: str = CONFIG) -> Path:
    return write_policy_project(
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
    root = _symbol_project(tmp_path, client)
    before = (root / "app/client.py").read_bytes()
    [finding] = violations(root, SELECT)
    assert finding.code == "VIS005"
    assert finding.severity is Severity.WARNING
    assert finding.source_module == "app.client"
    assert finding.target_module == "app.service"
    assert finding.path == "app/client.py"
    assert finding.expected == {
        "kind": "symbol",
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
    assert violations(_symbol_project(tmp_path, client), SELECT) == []


def test_import_locations_and_multiline_suppression_are_alias_specific(
    tmp_path: Path,
) -> None:
    root = _symbol_project(
        tmp_path,
        "from .service import (\n"
        "    _helper as helper,  # smelt: ignore[VIS005] -- Compatibility adapter.\n"
        "    _Service,\n)\n",
    )
    [finding] = violations(root, SELECT)
    assert (finding.line, finding.column) == (3, 5)
    assert finding.expected is not None
    assert finding.expected["private_target"] == "app.service._Service"


@pytest.mark.parametrize("policy", ["include", "ignore"])
def test_type_checking_policy_applies_to_imports_and_reads(
    tmp_path: Path, policy: str
) -> None:
    root = _symbol_project(
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
        config=CONFIG + f"architecture:\n  import_graph: {{type_checking: {policy}}}\n",
    )
    found = violations(root, SELECT)
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
    root = _symbol_project(tmp_path, "")
    write_policy_project(
        root,
        {
            "smelt.yml": CONFIG.replace("[app]", "[app, other]"),
            "app/__init__.py": initializer,
            "app/_impl.py": "def _helper(): pass\n",
            "other/__init__.py": "",
            "other/service.py": "def _helper(): pass\n",
        },
    )
    assert bool(violations(root, SELECT)) is flagged


def test_public_reexport_stays_public_and_private_module_names_are_separate(
    tmp_path: Path,
) -> None:
    root = _symbol_project(
        tmp_path,
        "from . import helper\nfrom . import _impl\nimport app._impl\n"
        "app._impl._helper()\n",
    )
    write_policy_project(
        root,
        {
            "app/__init__.py": "from ._impl import _helper as helper",
            "app/_impl.py": "def _helper(): pass\n",
        },
    )
    [finding] = violations(root, SELECT)
    assert finding.line == 4
    assert finding.expected is not None
    assert finding.expected["private_target"] == "app._impl._helper"


def test_test_files_remain_exclusively_with_smt420(tmp_path: Path) -> None:
    root = _symbol_project(tmp_path, "")
    write_policy_project(
        root,
        {"tests/test_service.py": "from app.service import _helper\n"},
    )
    found = violations(root, CheckOptions(select=("VIS005", "TST002")))
    assert [(v.code, v.path) for v in found] == [("TST002", "tests/test_service.py")]


def test_rule_is_explicit_and_supports_package_overrides(tmp_path: Path) -> None:
    root = _symbol_project(tmp_path, "from .service import _helper")
    assert violations(root) == []
    [finding] = violations(
        root, CheckOptions(select=("visibility/foreign-private-access",))
    )
    assert finding.severity is Severity.WARNING
    assert [v.code for v in violations(root, CheckOptions(select=("SMT33",)))] == [
        "VIS005"
    ]
    write_policy_project(
        root,
        {
            "smelt.yml": CONFIG
            + "rules: {visibility/foreign-private-access: error}\n"
            + "style:\n  overrides:\n    caller:\n      match: app\n"
            + "      rules: {visibility/foreign-private-access: off}\n"
        },
    )
    assert violations(root, SELECT) == []
    write_policy_project(
        root,
        {"smelt.yml": CONFIG + "rules: {visibility/foreign-private-access: error}\n"},
    )
    [finding] = violations(root)
    assert finding.severity is Severity.ERROR


def test_cross_module_definition_changes_invalidate_cached_decisions(
    tmp_path: Path,
) -> None:
    root = _symbol_project(tmp_path, "import app.service as s\ns._helper()")
    assert len(violations(root, SELECT)) == 1
    for definitions in ("", "from third_party import _helper\n", "_helper = 1\n"):
        write_policy_project(root, {"app/service.py": definitions})
        cached = violations(root, SELECT)
        fresh = violations(root, CheckOptions(select=("VIS005",), use_cache=False))
        assert cached == fresh
        assert len(cached) == (1 if definitions == "_helper = 1\n" else 0)


@pytest.mark.parametrize("with_init", [True, False])
def test_nested_relative_imports_and_namespace_source_layouts(
    tmp_path: Path, with_init: bool
) -> None:
    files = {
        "smelt.yml": "version: 2\nproject: {root_packages: [app], source_roots: [src]}\n",
        "src/app/feature/client.py": "from ..service import _helper\n",
        "src/app/service.py": "def _helper(): pass\n",
    }
    if with_init:
        files.update({"src/app/__init__.py": "", "src/app/feature/__init__.py": ""})
    root = write_policy_project(tmp_path, files)
    [finding] = violations(root, SELECT)
    assert finding.path == "src/app/feature/client.py"
    assert finding.expected is not None
    assert finding.expected["private_target"] == "app.service._helper"


def test_reexported_module_aliases_and_private_symbol_chains(tmp_path: Path) -> None:
    root = _symbol_project(
        tmp_path,
        "from .bridge import service as s\ns._helper()\n"
        "from .bridge import _helper\nfrom .bridge import _external\n",
    )
    write_policy_project(
        root,
        {
            "app/bridge.py": "from . import service\nfrom .service import _helper\n"
            "from third_party import _external\n"
        },
    )
    found = [v for v in violations(root, SELECT) if v.path == "app/client.py"]
    assert [v.line for v in found] == [2, 3]
    assert all(
        v.expected and v.expected["defined_in"] == "app.service._helper" for v in found
    )


OWNER = """\
class Images:
    def retain_message(self, message):
        return message

class SessionLog:
    _shared = 1

    def __init__(self):
        self._images = Images()
        self._count = 0

    def _flush(self):
        return None
"""


@pytest.mark.parametrize(
    ("expression", "operation", "attribute"),
    [
        ("return log._images", "read", "_images"),
        ("log._images = None", "write", "_images"),
        ("log._flush()", "call", "_flush"),
        ("log._count += 1", "update", "_count"),
        ("del log._images", "delete", "_images"),
        ("return log._images.retain_message('message')", "read", "_images"),
    ],
)
def test_reports_resolved_private_operations(
    tmp_path: Path, expression: str, operation: str, attribute: str
) -> None:
    source = f"from app.session import SessionLog\ndef persist(log: SessionLog):\n    {expression}\n"
    root = _attribute_project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line) == ("VIS005", "app/client.py", 3)
    assert finding.severity == Severity.WARNING
    assert finding.target_module == f"app.session.SessionLog.{attribute}"
    assert finding.expected == {
        "kind": "attribute",
        "boundary": "module",
        "owner_module": "app.session",
        "owner_class": "app.session.SessionLog",
        "receiver_type": "app.session.SessionLog",
        "attribute": attribute,
        "operation": operation,
        "automatic_rewrite": False,
    }
    assert (root / "app/client.py").read_text() == source


@pytest.mark.parametrize(
    "declaration",
    [
        "def __init__(self, log: SessionLog):\n        self.log = log",
        "def __init__(self):\n        self.log = SessionLog()",
        "log: SessionLog",
        "@property\n    def log(self) -> SessionLog:\n        return resolve_log()",
        "@cached_property\n    def log(self) -> 'SessionLog':\n        return resolve_log()",
    ],
)
def test_resolves_stored_collaborators_and_property_return_annotations(
    tmp_path: Path, declaration: str
) -> None:
    source = f"from functools import cached_property\nfrom app.session import SessionLog\nclass Inbox:\n    {declaration}\n\n    async def persist(self, message):\n        return await self.log._images.retain_message(message)\n"
    [finding] = violations(_attribute_project(tmp_path, source), SELECT)
    assert finding.target_module == "app.session.SessionLog._images"


@pytest.mark.parametrize(
    "source",
    [
        "from app.session import SessionLog\ndef persist():\n    log = SessionLog()\n    return log._images\n",
        "from app.session import SessionLog\ndef persist():\n    log: SessionLog\n    return log._images\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    alias = log\n    return alias._images\n",
        "from app.session import SessionLog\nlog = SessionLog()\nvalue = log._images\n",
        "from app.session import SessionLog\nvalue = SessionLog()._images\n",
        "from app.session import SessionLog\nvalue = SessionLog._shared\n",
        "import app.session as session\ndef persist(log: session.SessionLog):\n    return log._images\n",
        "from .session import SessionLog as Log\ndef persist(log: 'Log'):\n    return log._images\n",
        "from app import PublicLog as Log\ndef persist(log: Log):\n    return log._images\n",
        "from typing import Annotated\nfrom app.session import SessionLog\ndef persist(log: Annotated[SessionLog, 'scope']):\n    return log._images\n",
    ],
)
def test_resolves_annotations_constructors_aliases_and_reexports(
    tmp_path: Path, source: str
) -> None:
    root = _attribute_project(tmp_path, source)
    write_policy_project(
        root, {"app/__init__.py": "from .session import SessionLog as PublicLog\n"}
    )
    [finding] = violations(root, SELECT)
    attribute = "_shared" if "._shared" in source else "_images"
    assert finding.target_module == f"app.session.SessionLog.{attribute}"


def test_inherited_members_keep_the_defining_class_owner(tmp_path: Path) -> None:
    root = _attribute_project(
        tmp_path,
        "from app.special import SpecialLog\ndef persist(log: SpecialLog):\n    return log._images\n",
    )
    write_policy_project(
        root,
        {
            "app/special.py": "from .session import SessionLog\nclass SpecialLog(SessionLog):\n    pass\n"
        },
    )
    [finding] = violations(root, SELECT)
    assert finding.expected is not None
    assert finding.expected["receiver_type"] == "app.special.SpecialLog"
    assert finding.expected["owner_class"] == "app.session.SessionLog"


def test_private_property_ownership_is_reported(tmp_path: Path) -> None:
    root = _attribute_project(
        tmp_path,
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n",
        owner=OWNER.replace(
            "    def __init__(self):\n        self._images = Images()\n        self._count = 0",
            "    @property\n    def _images(self) -> Images:\n        return Images()",
        ),
    )
    [finding] = violations(root, SELECT)
    assert finding.target_module == "app.session.SessionLog._images"


def test_same_module_cooperation_and_own_inherited_receiver_accesses_are_allowed(
    tmp_path: Path,
) -> None:
    source = "from app.session import SessionLog\nclass SpecialLog(SessionLog):\n    def persist(self):\n        return self._images\n    @classmethod\n    def shared(cls):\n        return cls._shared\n"
    root = _attribute_project(
        tmp_path,
        source,
        owner=OWNER + "\ndef cooperate(log: SessionLog):\n    return log._images\n",
    )
    assert violations(root, SELECT) == []


def test_staticmethod_parameter_named_self_is_not_an_own_receiver(
    tmp_path: Path,
) -> None:
    root = _attribute_project(
        tmp_path,
        "from app.session import SessionLog\nclass Inbox:\n    @staticmethod\n    def persist(self: SessionLog):\n        return self._images\n",
    )
    [finding] = violations(root, SELECT)
    assert finding.target_module == "app.session.SessionLog._images"


@pytest.mark.parametrize(
    "source",
    [
        "def persist(log):\n    return log._images\n",
        "from app.session import SessionLog\ndef persist():\n    log = factory()\n    return log._images\n",
        "from app.session import SessionLog\ndef factory() -> SessionLog:\n    return SessionLog()\ndef persist():\n    return factory()._images\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    log = unknown()\n    return log._images\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    if condition:\n        log = unknown()\n    return log._images\n",
        "from app.session import SessionLog\ndef persist():\n    if condition:\n        log = SessionLog()\n    return log._images\n",
        "from app.session import SessionLog\ndef persist():\n    SessionLog = factory\n    return SessionLog()._images\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log.__dict__\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._unknown\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog | Other):\n    return log._images\n",
        "from app.session import SessionLog\ndef persist(log: list[SessionLog]):\n    return log._images\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return [log._images for log in values]\n",
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return lambda log: log._images\n",
        "from app.session import SessionLog\ndef persist():\n    from somewhere import SessionLog\n    return SessionLog()._images\n",
    ],
)
def test_skips_unresolved_shadowed_rebound_dynamic_and_dunder_accesses(
    tmp_path: Path, source: str
) -> None:
    assert violations(_attribute_project(tmp_path, source), SELECT) == []


def test_skips_ambiguous_inherited_ownership(tmp_path: Path) -> None:
    root = _attribute_project(
        tmp_path,
        "from app.special import SpecialLog\ndef persist(log: SpecialLog):\n    return log._images\n",
    )
    write_policy_project(
        root,
        {
            "app/special.py": "from .session import SessionLog\nclass Other: pass\nclass SpecialLog(Other, SessionLog): pass\n"
        },
    )
    assert violations(root, SELECT) == []


def test_unknown_class_decorators_do_not_establish_receiver_types(
    tmp_path: Path,
) -> None:
    root = _attribute_project(
        tmp_path,
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n",
        owner=OWNER.replace("class SessionLog:", "@replace_class\nclass SessionLog:"),
    )
    assert violations(root, SELECT) == []


def test_global_package_boundary_keeps_subpackages_separate(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _attribute_project(
        tmp_path, source, settings="style: {private_attribute_boundary: package}\n"
    )
    write_policy_project(
        root, {"app/runtime/__init__.py": "", "app/runtime/inbox.py": source}
    )
    [finding] = violations(root, SELECT)
    assert finding.path == "app/runtime/inbox.py"


def test_closest_package_boundary_overrides_the_global_default(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _attribute_project(
        tmp_path,
        source,
        settings="""\
style:
  private_attribute_boundary: package
  overrides:
    strict: {match: app, private_attribute_boundary: module}
    inner: {match: app.runtime, private_attribute_boundary: package}
""",
    )
    write_policy_project(
        root,
        {
            "app/runtime/__init__.py": "",
            "app/runtime/logs.py": OWNER,
            "app/runtime/inbox.py": source.replace("app.session", "app.runtime.logs"),
        },
    )
    [finding] = violations(root, SELECT)
    assert finding.path == "app/client.py"


def test_package_initializer_shares_its_package_boundary(tmp_path: Path) -> None:
    root = _attribute_project(
        tmp_path,
        "from app import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n",
        settings="style: {private_attribute_boundary: package}\n",
    )
    write_policy_project(root, {"app/__init__.py": OWNER})
    assert violations(root, SELECT) == []


def test_opt_in_package_off_suppression_and_test_exclusion(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _attribute_project(
        tmp_path, source, settings="style: {private_attribute_boundary: module}\n"
    )
    assert [v.code for v in violations(root)] == ["VIS005"]
    write_policy_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {visibility/foreign-private-access: error}
style:
  overrides:
    quiet: {match: app.quiet, rules: {visibility/foreign-private-access: off}}
""",
            "app/quiet/__init__.py": "",
            "app/quiet/client.py": source,
            "app/intentional.py": source.replace(
                "return log._images",
                "return log._images  # smelt: ignore[VIS005] -- reviewed storage adapter",
            ),
            "tests/test_client.py": source,
        },
    )
    [finding] = violations(root)
    assert (finding.path, finding.severity) == ("app/client.py", Severity.ERROR)
    assert violations(root, CheckOptions(use_cache=False)) == [finding]


def test_cache_tracks_owner_changes_in_unchanged_consumers(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _attribute_project(tmp_path, source)
    assert len(violations(root, SELECT)) == 1
    (root / "app/session.py").write_text(
        OWNER.replace("self._images", "self.images"), encoding="utf-8"
    )
    assert violations(root, SELECT) == []
    assert violations(root, CheckOptions(select=("VIS005",), use_cache=False)) == []


def test_cache_tracks_reexports_and_removal_of_the_known_owner(tmp_path: Path) -> None:
    root = _attribute_project(
        tmp_path,
        "from app import PublicLog\ndef persist(log: PublicLog):\n    return log._images\n",
    )
    write_policy_project(
        root,
        {
            "app/__init__.py": "from .session import SessionLog as PublicLog\n",
            "app/alternate.py": OWNER,
        },
    )
    [before] = violations(root, SELECT)
    assert before.target_module == "app.session.SessionLog._images"
    (root / "app/__init__.py").write_text(
        "from .alternate import SessionLog as PublicLog\n", encoding="utf-8"
    )
    [after] = violations(root, SELECT)
    assert after.target_module == "app.alternate.SessionLog._images"
    assert violations(root, CheckOptions(select=("VIS005",), use_cache=False)) == [
        after
    ]
    (root / "app/alternate.py").unlink()
    assert violations(root, SELECT) == []
    assert violations(root, CheckOptions(select=("VIS005",), use_cache=False)) == []


@pytest.mark.parametrize(
    "settings",
    [
        "style: {private_attribute_boundary: tree}",
        "style: {overrides: {bad: {match: app, private_attribute_boundary: tree}}}",
        "style:\n  overrides:\n    a: {match: app, private_attribute_boundary: module}\n    b: {match: app, private_attribute_boundary: package}\n",
    ],
)
def test_rejects_invalid_or_conflicting_boundaries(
    tmp_path: Path, settings: str
) -> None:
    with pytest.raises(ConfigError, match=r"private.attribute"):
        violations(_attribute_project(tmp_path, "", settings=settings), SELECT)


def _attribute_project(
    tmp_path: Path, source: str, *, settings: str = "", owner: str = OWNER
) -> Path:
    return write_policy_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + settings,
            "app/__init__.py": "",
            "app/session.py": owner,
            "app/client.py": source,
        },
    )


def test_attribute_package_boundary_does_not_relax_private_symbols(
    tmp_path: Path,
) -> None:
    source = (
        "from app.session import SessionLog, _helper\n"
        "def persist(log: SessionLog):\n"
        "    return _helper(log._images)\n"
    )
    root = _attribute_project(
        tmp_path,
        source,
        settings="style: {private_attribute_boundary: package}\n",
        owner=OWNER + "def _helper(value):\n    return value\n",
    )
    found = violations(root, SELECT)
    assert [(v.expected or {})["kind"] for v in found] == ["symbol"]
    root = _attribute_project(
        tmp_path,
        source,
        owner=OWNER + "def _helper(value):\n    return value\n",
    )
    found = violations(root, SELECT)
    assert sorted((v.expected or {})["kind"] for v in found) == ["attribute", "symbol"]
