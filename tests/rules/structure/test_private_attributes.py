from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT365",))
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
    root = _project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line) == ("SMT365", "app/client.py", 3)
    assert finding.severity == Severity.WARNING
    assert finding.target_module == f"app.session.SessionLog.{attribute}"
    assert finding.expected == {
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
    [finding] = violations(_project(tmp_path, source), SELECT)
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
    root = _project(tmp_path, source)
    write_project(
        root, {"app/__init__.py": "from .session import SessionLog as PublicLog\n"}
    )
    [finding] = violations(root, SELECT)
    attribute = "_shared" if "._shared" in source else "_images"
    assert finding.target_module == f"app.session.SessionLog.{attribute}"


def test_inherited_members_keep_the_defining_class_owner(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from app.special import SpecialLog\ndef persist(log: SpecialLog):\n    return log._images\n",
    )
    write_project(
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
    root = _project(
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
    root = _project(
        tmp_path,
        source,
        owner=OWNER + "\ndef cooperate(log: SessionLog):\n    return log._images\n",
    )
    assert violations(root, SELECT) == []


def test_staticmethod_parameter_named_self_is_not_an_own_receiver(
    tmp_path: Path,
) -> None:
    root = _project(
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
    assert violations(_project(tmp_path, source), SELECT) == []


def test_skips_ambiguous_inherited_ownership(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from app.special import SpecialLog\ndef persist(log: SpecialLog):\n    return log._images\n",
    )
    write_project(
        root,
        {
            "app/special.py": "from .session import SessionLog\nclass Other: pass\nclass SpecialLog(Other, SessionLog): pass\n"
        },
    )
    assert violations(root, SELECT) == []


def test_unknown_class_decorators_do_not_establish_receiver_types(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n",
        owner=OWNER.replace("class SessionLog:", "@replace_class\nclass SessionLog:"),
    )
    assert violations(root, SELECT) == []


def test_global_package_boundary_keeps_subpackages_separate(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _project(
        tmp_path, source, settings="structure: {private_attribute_boundary: package}\n"
    )
    write_project(root, {"app/runtime/__init__.py": "", "app/runtime/inbox.py": source})
    [finding] = violations(root, SELECT)
    assert finding.path == "app/runtime/inbox.py"


def test_closest_package_boundary_overrides_the_global_default(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _project(
        tmp_path,
        source,
        settings="""\
structure:
  private_attribute_boundary: package
  contracts:
    strict: {match: app, private_attribute_boundary: module}
    inner: {match: app.runtime, private_attribute_boundary: package}
""",
    )
    write_project(
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
    root = _project(
        tmp_path,
        "from app import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n",
        settings="structure: {private_attribute_boundary: package}\n",
    )
    write_project(root, {"app/__init__.py": OWNER})
    assert violations(root, SELECT) == []


def test_opt_in_package_off_suppression_and_test_exclusion(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _project(
        tmp_path, source, settings="structure: {private_attribute_boundary: module}\n"
    )
    assert violations(root) == []
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {cross-module-private-attribute: error}
structure:
  contracts:
    quiet: {match: app.quiet, rules: {cross-module-private-attribute: off}}
""",
            "app/quiet/__init__.py": "",
            "app/quiet/client.py": source,
            "app/intentional.py": source.replace(
                "return log._images",
                "return log._images  # smelt: ignore[SMT365] -- reviewed storage adapter",
            ),
            "tests/test_client.py": source,
        },
    )
    [finding] = violations(root)
    assert (finding.path, finding.severity) == ("app/client.py", Severity.ERROR)
    assert violations(root, CheckOptions(use_cache=False)) == [finding]


def test_cache_tracks_owner_changes_in_unchanged_consumers(tmp_path: Path) -> None:
    source = "from app.session import SessionLog\ndef persist(log: SessionLog):\n    return log._images\n"
    root = _project(tmp_path, source)
    assert len(violations(root, SELECT)) == 1
    (root / "app/session.py").write_text(
        OWNER.replace("self._images", "self.images"), encoding="utf-8"
    )
    assert violations(root, SELECT) == []
    assert violations(root, CheckOptions(select=("SMT365",), use_cache=False)) == []


def test_cache_tracks_reexports_and_removal_of_the_known_owner(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from app import PublicLog\ndef persist(log: PublicLog):\n    return log._images\n",
    )
    write_project(
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
    assert violations(root, CheckOptions(select=("SMT365",), use_cache=False)) == [
        after
    ]
    (root / "app/alternate.py").unlink()
    assert violations(root, SELECT) == []
    assert violations(root, CheckOptions(select=("SMT365",), use_cache=False)) == []


@pytest.mark.parametrize(
    "settings",
    [
        "structure: {private_attribute_boundary: tree}",
        "structure: {contracts: {bad: {match: app, private_attribute_boundary: tree}}}",
        "structure:\n  contracts:\n    a: {match: app, private_attribute_boundary: module}\n    b: {match: app, private_attribute_boundary: package}\n",
    ],
)
def test_rejects_invalid_or_conflicting_boundaries(
    tmp_path: Path, settings: str
) -> None:
    with pytest.raises(ConfigError, match=r"private.attribute"):
        violations(_project(tmp_path, "", settings=settings), SELECT)


def _project(
    tmp_path: Path, source: str, *, settings: str = "", owner: str = OWNER
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + settings,
            "app/__init__.py": "",
            "app/session.py": owner,
            "app/client.py": source,
        },
    )
