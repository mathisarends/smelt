from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("TST011",))


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
        "TST011",
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
    assert [(v.code, v.line) for v in violations(tmp_path, OPTIONS)] == [("TST011", 6)]


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
    assert [v.code for v in violations(tmp_path, OPTIONS)] == ["TST011"]
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
        tmp_path, {"smelt.yml": CONFIG + "rules: {tests/nondeterministic-data: off}\n"}
    )
    assert violations(tmp_path, OPTIONS) == []
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + "rules: {tests/nondeterministic-data: warning}\n",
            "tests/test_create.py": source.replace(
                "assert uuid4()",
                "assert uuid4()  # smelt: ignore[TST011] -- Exercises the UUID generator itself.",
            ),
        },
    )
    assert violations(tmp_path) == []


@pytest.mark.parametrize(
    ("imports", "call", "qualified"),
    [
        ("from datetime import datetime", "datetime.now()", "datetime.datetime.now"),
        ("from datetime import datetime", "datetime.now(UTC)", "datetime.datetime.now"),
        (
            "from datetime import datetime",
            "datetime.utcnow()",
            "datetime.datetime.utcnow",
        ),
        (
            "from datetime import datetime",
            "datetime.today()",
            "datetime.datetime.today",
        ),
        ("from datetime import date", "date.today()", "datetime.date.today"),
        ("import datetime", "datetime.datetime.now()", "datetime.datetime.now"),
        ("import datetime as dt", "dt.date.today()", "datetime.date.today"),
        (
            "from datetime import datetime as clock",
            "clock.now()",
            "datetime.datetime.now",
        ),
        ("import time", "time.time()", "time.time"),
        ("from time import time_ns", "time_ns()", "time.time_ns"),
    ],
)
def test_wall_clock_reads_are_reviewed(
    tmp_path: Path, imports: str, call: str, qualified: str
) -> None:
    source = f"{imports}\n\ndef test_expired():\n    value = {call}\n    assert value\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_lease.py": source},
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert (finding.code, finding.line) == ("TST011", 4)
    assert finding.expected is not None
    assert finding.expected["generators"] == [qualified]
    assert "wall-clock" in finding.message


def test_clock_and_random_reads_on_one_line_are_one_finding(tmp_path: Path) -> None:
    source = "from datetime import datetime\nfrom uuid import uuid4\n\ndef test_create():\n    lease = create(uuid4(), datetime.now())\n    assert lease\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_lease.py": source},
    )
    [finding] = violations(tmp_path, OPTIONS)
    assert finding.line == 5
    assert finding.expected is not None
    assert finding.expected["generators"] == ["datetime.datetime.now", "uuid.uuid4"]


def test_clock_reads_in_fixtures_and_helpers_are_reviewed(tmp_path: Path) -> None:
    source = "import time\nimport pytest\n\n@pytest.fixture\ndef started():\n    return time.time()\n\ndef make_lease():\n    return Lease(time.time())\n\ndef test_lease(started):\n    assert make_lease()\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_lease.py": source},
    )
    assert [v.line for v in violations(tmp_path, OPTIONS)] == [6, 9]


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\nfrom freezegun import freeze_time\n\ndef test_expired():\n    with freeze_time('2026-01-01'):\n        assert datetime.now()\n",
        "from datetime import datetime\nimport freezegun\n\n@freezegun.freeze_time('2026-01-01')\ndef test_expired():\n    assert datetime.now()\n",
        "from datetime import datetime\nfrom freezegun import freeze_time\n\n@freeze_time('2026-01-01')\nclass TestLease:\n    def test_expired(self):\n        assert datetime.now()\n",
        "import time\nimport time_machine\n\nasync def test_expired():\n    async with time_machine.travel(0):\n        assert time.time()\n",
        "from datetime import datetime\nimport pytest\n\n@pytest.mark.freeze_time('2026-01-01')\ndef test_expired():\n    assert datetime.now()\n",
        "from datetime import datetime\n\ndef test_expired(freezer):\n    assert datetime.now()\n",
        "import time\n\ndef test_deadline():\n    deadline = time.monotonic() + 5\n    assert time.perf_counter() < deadline\n",
        "from datetime import UTC, datetime\n\ndef test_expired():\n    assert datetime(2026, 1, 1, tzinfo=UTC)\n",
    ],
)
def test_frozen_clocks_deadlines_and_fixed_instants_are_preserved(
    tmp_path: Path, source: str
) -> None:
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_lease.py": source},
    )
    assert violations(tmp_path, OPTIONS) == []


def test_clock_read_after_a_freeze_context_is_reviewed(tmp_path: Path) -> None:
    source = "from datetime import datetime\nfrom freezegun import freeze_time\n\ndef test_expired():\n    with freeze_time('2026-01-01'):\n        frozen = datetime.now()\n    assert datetime.now() > frozen\n"
    write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "tests/test_lease.py": source},
    )
    assert [v.line for v in violations(tmp_path, OPTIONS)] == [7]
