from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("from app.service import _helper", True),
        ("from app.service import _helper as public", True),
        ("import app._internal", True),
        ("from app import _internal", True),
        ("from app import service as s\ndef test_run(): s._helper()", True),
        ("from app.service import public", False),
        ("from app.service import __version__", False),
        ("from unittest.mock import _ANY", False),
        ("def _helper(): pass\ndef test_run(): _helper()", False),
        ("from app import service as s\ndef test_run(s): s._helper()", False),
        (
            "from app import service as s\ndef test_run():\n    s = fake\n    s._helper()",
            False,
        ),
    ],
)
def test_private_production_access(tmp_path: Path, source: str, flagged: bool) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {forbid_private_imports: true}",
            "app/__init__.py": "",
            "app/service.py": "def _helper(): pass\ndef public(): pass",
            "app/_internal.py": "",
            "tests/test_service.py": source,
        },
    )
    found = violations(tmp_path, CheckOptions(select=("SMT420",)))
    assert bool(found) is flagged
    if found:
        assert found[0].expected is not None
        assert found[0].expected["private_target"].startswith("app.")


@pytest.mark.parametrize(
    ("source", "provenance"),
    [
        (
            "from app.tabs import Tabs\ndef test_run():\n    tabs = Tabs()\n    tabs._require_control()",
            "constructor",
        ),
        (
            "from app.tabs import Tabs as Controller\ndef test_run(tabs: Controller): tabs._require_control()",
            "annotation",
        ),
        (
            "from app.tabs import Tabs\ndef test_run():\n    tabs: Tabs = build()\n    tabs._require_control()",
            "annotation",
        ),
        (
            "from app.tabs import Tabs\ndef test_run():\n    tabs = Tabs()\n    alias = tabs\n    alias._require_control()",
            "constructor",
        ),
        (
            "import pytest\nfrom app.tabs import Tabs\n@pytest.fixture\ndef tabs() -> Tabs: return Tabs()\ndef test_run(tabs): tabs._require_control()",
            "fixture-return",
        ),
    ],
)
def test_private_instance_reads_have_explicit_provenance(
    tmp_path: Path, source: str, provenance: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {forbid_private_imports: true}",
            "app/__init__.py": "",
            "app/tabs.py": "class Tabs:\n    def _require_control(self): pass",
            "tests/test_tabs.py": source,
        },
    )
    [finding] = violations(tmp_path, CheckOptions(select=("SMT420",)))
    assert finding.expected is not None
    assert finding.expected["private_target"] == "app.tabs.Tabs._require_control"
    assert finding.expected["provenance"] == provenance
    assert finding.expected["access"] == "instance"


@pytest.mark.parametrize(
    "body",
    [
        "tabs = unknown()\n    tabs._require_control()",
        "tabs = Tabs()\n    tabs = unknown()\n    tabs._require_control()",
        "tabs = Tabs()\n    tabs._secret = 'immutable-probe'",
        "class Fake:\n        def _require_control(self): pass\n    tabs = Fake()\n    tabs._require_control()",
    ],
)
def test_unknown_receivers_local_fakes_and_instance_writes_are_excluded(
    tmp_path: Path, body: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {forbid_private_imports: true}",
            "app/__init__.py": "",
            "app/tabs.py": "class Tabs: pass",
            "tests/test_tabs.py": "from app.tabs import Tabs\ndef test_run():\n    "
            + body,
        },
    )
    assert violations(tmp_path, CheckOptions(select=("SMT420",))) == []


def test_annotated_conftest_fixture_scope_and_override(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {forbid_private_imports: true}",
            "app/__init__.py": "",
            "app/tabs.py": "class Tabs: pass",
            "tests/conftest.py": "import pytest\nfrom app.tabs import Tabs\nfrom collections.abc import Iterator\n@pytest.fixture\ndef tabs() -> Iterator[Tabs]:\n    yield Tabs()",
            "tests/test_tabs.py": "def test_run(tabs): tabs._require_control()",
            "tests/local/conftest.py": "import pytest\n@pytest.fixture\ndef tabs(): return Fake()",
            "tests/local/test_tabs.py": "def test_run(tabs): tabs._require_control()",
            "tests/neighbor/test_tabs.py": "def test_run(tabs): tabs._require_control()",
        },
    )
    found = violations(tmp_path, CheckOptions(select=("SMT420",)))
    assert {finding.path for finding in found} == {
        "tests/test_tabs.py",
        "tests/neighbor/test_tabs.py",
    }


@pytest.mark.parametrize(
    "fixture",
    [
        "@pytest.fixture\ndef resource():\n    tabs = Tabs()\n    try:\n        yield tabs, []\n    finally:\n        tabs.close()",
        "@pytest.fixture\ndef resource():\n    return Tabs(), []",
        "@pytest.fixture\ndef resource() -> tuple[Tabs, list]:\n    return build()",
    ],
)
def test_returned_fixture_tuple_preserves_known_production_receiver(
    tmp_path: Path, fixture: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {forbid_private_imports: true}",
            "app/__init__.py": "from .tabs import Tabs as Tabs",
            "app/tabs.py": "class Tabs: pass",
            "tests/test_tabs.py": "import pytest\nfrom app import Tabs\n"
            + fixture
            + "\ndef test_run(resource):\n    tabs, _ = resource\n    tabs._require_control()",
        },
    )
    [finding] = violations(tmp_path, CheckOptions(select=("SMT420",)))
    assert finding.expected is not None
    assert finding.expected["provenance"] in {"fixture-return", "fixture-inferred"}
    assert finding.expected["private_target"].endswith("Tabs._require_control")


def test_private_class_import_does_not_repeat_for_every_public_instance_operation(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {forbid_private_imports: true}",
            "app/__init__.py": "",
            "app/service.py": "class _Service: pass",
            "tests/test_service.py": "from app.service import _Service\ndef test_run():\n    service = _Service()\n    service.run()\n    assert service.status == 'ready'\n    service.__str__()\n    service._inspect()",
        },
    )
    found = violations(tmp_path, CheckOptions(select=("SMT420",)))
    assert [(finding.line, finding.target_module) for finding in found] == [
        (1, "app.service._Service"),
        (7, "app.service._Service._inspect"),
    ]
