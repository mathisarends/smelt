from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("SMT431",))


@pytest.mark.parametrize(
    ("imports", "call", "qualified"),
    [
        ("from uuid import uuid4", "uuid4()", "uuid.uuid4"),
        ("from uuid import uuid4 as new_id", "new_id()", "uuid.uuid4"),
        ("import uuid as ids", "ids.uuid1()", "uuid.uuid1"),
        ("import random", "random.randint(1, 9)", "random.randint"),
        ("from secrets import token_hex", "token_hex(8)", "secrets.token_hex"),
        ("import os", "os.urandom(4)", "os.urandom"),
    ],
)
def test_known_test_data_generators_are_reviewed(
    tmp_path: Path, imports: str, call: str, qualified: str
) -> None:
    source = f"{imports}\n\ndef test_create():\n    value = {call}\n    assert value\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_create.py": source},
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert (finding.code, finding.path, finding.line) == (
        "SMT431",
        "tests/test_create.py",
        4,
    )
    assert finding.expected is not None
    assert finding.expected["generators"] == [qualified]
    assert (tmp_path / "tests/test_create.py").read_text() == source


def test_issue_60_groups_two_ids_and_preserves_falsy_uuid_regression(
    tmp_path: Path,
) -> None:
    source = "from uuid import UUID, uuid4\nclass FalsyUuid(UUID):\n    def __bool__(self): return False\nasync def test_create():\n    lease_id = FalsyUuid(int=0)\n    lease = await create(uuid4(), uuid4(), lease_id=lease_id)\n    assert lease.id is lease_id\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_create.py": source},
    )
    assert [(v.code, v.line) for v in violations(tmp_path, OPTIONS)] == [("SMT431", 6)]


def test_fixed_seeded_unknown_shadowed_and_production_calls_are_preserved(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "app/ids.py": "from uuid import uuid4\nvalue = uuid4()\n",
            "tests/test_create.py": "from uuid import UUID\nimport random\nfrom uuid import uuid4\ndef test_create(uuid4):\n    fixed = UUID(int=1)\n    rng = random.Random(42)\n    assert rng.random() >= 0\n    assert uuid4()\n",
        },
    )
    assert violations(tmp_path, OPTIONS) == []


def test_fixture_setup_is_reviewed_and_quality_exclusions_apply(tmp_path: Path) -> None:
    source = "import pytest\nfrom uuid import uuid4\n@pytest.fixture\ndef record_id():\n    return uuid4()\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/conftest.py": source},
    )
    assert [v.code for v in violations(tmp_path, OPTIONS)] == ["SMT431"]
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG + "tests: {quality_exclude: [tests/conftest.py]}\n"},
    )
    assert violations(tmp_path, OPTIONS) == []


def test_opt_in_off_and_reasoned_suppression(tmp_path: Path) -> None:
    source = "from uuid import uuid4\ndef test_create():\n    assert uuid4()\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_create.py": source},
    )
    assert violations(tmp_path) == []
    write_project(
        tmp_path, {"smelt.yml": CONFIG + "rules: {nondeterministic-test-data: off}\n"}
    )
    assert violations(tmp_path, OPTIONS) == []
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + "rules: {nondeterministic-test-data: warning}\n",
            "tests/test_create.py": source.replace(
                "assert uuid4()",
                "assert uuid4()  # smelt: ignore[SMT431] -- Exercises the UUID generator itself.",
            ),
        },
    )
    assert violations(tmp_path) == []
