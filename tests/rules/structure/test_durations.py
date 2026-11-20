from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("MOD010",))


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/frames.py": source},
    )


def _names(root: Path) -> list[tuple[int | None, str, str]]:
    return [
        (item.line, item.expected["kind"], item.expected["name"])
        for item in violations(root, SELECT)
        if item.expected is not None and item.expected["kind"] != "literal"
    ]


def _literals(root: Path) -> list[tuple[int | None, str, str]]:
    return [
        (item.line, item.expected["value"], item.expected["target"])
        for item in violations(root, SELECT)
        if item.expected is not None and item.expected["kind"] == "literal"
    ]


def test_reports_unitless_constant_from_the_issue(tmp_path: Path) -> None:
    (finding,) = violations(_project(tmp_path, "_FRAME_TIMEOUT = 2.0\n"), SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "MOD010",
        "app/frames.py",
        1,
        1,
    )
    assert finding.message == "Constant _FRAME_TIMEOUT holds a duration without a unit"
    assert finding.expected == {
        "kind": "constant",
        "name": "_FRAME_TIMEOUT",
        "suggested_name": "_FRAME_TIMEOUT_SECONDS",
        "automatic_rewrite": False,
    }
    assert finding.hint == (
        "Name it _FRAME_TIMEOUT_SECONDS or after its actual unit, "
        "or type it as datetime.timedelta."
    )


def test_reports_fields_and_parameters(tmp_path: Path) -> None:
    source = (
        "from typing import Annotated, Final\n\n"
        "from pydantic import BaseModel, Field\n\n"
        "RETRY_DELAY: Final[int] = 3\n\n\n"
        "class Policy(BaseModel):\n"
        "    poll_interval: float | None = None\n"
        "    quiet_duration: Annotated[int, Field(ge=0)] = 300\n"
        "    wait = 5\n\n\n"
        "async def load(url, timeout: 'float' = 10, *, settle_delay=0.5):\n"
        "    ...\n"
    )

    assert _names(_project(tmp_path, source)) == [
        (5, "constant", "RETRY_DELAY"),
        (9, "field", "poll_interval"),
        (10, "field", "quiet_duration"),
        (11, "field", "wait"),
        (14, "parameter", "timeout"),
        (14, "parameter", "settle_delay"),
    ]


def test_accepts_units_timedeltas_and_other_names(tmp_path: Path) -> None:
    source = (
        "from datetime import timedelta\n\n"
        "_TIMEOUT_SECONDS = 5\n"
        "quiet_ms: int = 300\n"
        "TIMEOUT_MESSAGE = 'timed out'\n"
        "IS_TIMEOUT = True\n"
        "MAX_WAITERS = 4\n"
        "frame_timeout = timedelta(seconds=2)\n\n\n"
        "def load(timeout: timedelta, wait_ms: int = 300, waiter=1):\n"
        "    local_timeout = 5\n"
        "    return local_timeout\n"
    )

    assert _names(_project(tmp_path, source)) == []


def test_skips_override_and_overload_signatures(tmp_path: Path) -> None:
    source = (
        "from typing import overload, override\n\n\n"
        "class Client(Base):\n"
        "    @override\n"
        "    def connect(self, timeout: float) -> None:\n"
        "        ...\n\n"
        "    @overload\n"
        "    def read(self, timeout: int) -> bytes: ...\n"
    )

    assert _names(_project(tmp_path, source)) == []


def test_names_in_tests_are_not_checked(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_frames.py": "TIMEOUT = 5\n",
        },
    )

    assert _names(root) == []


def test_names_are_opt_in(tmp_path: Path) -> None:
    source = "_TIMEOUT = 5\n"

    assert [
        item for item in violations(_project(tmp_path, source)) if item.code == "MOD010"
    ] == []
    enabled = _project(
        tmp_path / "enabled",
        source,
        config=CONFIG + "rules: {modeling/implicit-duration-unit: warning}\n",
    )
    assert [item.code for item in violations(enabled)].count("MOD010") == 1


def test_reports_inline_timeout_from_the_issue(tmp_path: Path) -> None:
    source = (
        "import asyncio\n\n\n"
        "async def settle(page):\n"
        "    async with asyncio.timeout(2.5):\n"
        "        await page.settle()\n"
    )

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "MOD010",
        "app/frames.py",
        5,
        32,
    )
    assert finding.message == "Inline duration 2.5 in asyncio.timeout()"
    assert finding.expected == {
        "kind": "literal",
        "value": "2.5",
        "target": "asyncio.timeout()",
        "automatic_rewrite": False,
    }
    assert finding.hint == (
        "Name 2.5 as a constant or setting that states its purpose and unit, "
        "and reuse it at matching call sites."
    )


def test_reports_aliases_keywords_and_clock_deadlines(tmp_path: Path) -> None:
    source = (
        "import asyncio as aio\n"
        "import time\n"
        "from asyncio import sleep, wait_for\n\n\n"
        "async def run(work, loop):\n"
        "    await sleep(0.1)\n"
        "    await wait_for(work(), 10)\n"
        "    await aio.wait_for(work(), timeout=3)\n"
        "    await aio.wait([work()], timeout=1)\n"
        "    time.sleep(2)\n"
        "    deadline = aio.get_running_loop().time() + 10\n"
        "    other = 5 + time.monotonic()\n"
        "    return loop.time() + 1.5, deadline, other\n"
    )

    assert _literals(_project(tmp_path, source)) == [
        (7, "0.1", "asyncio.sleep()"),
        (8, "10", "asyncio.wait_for()"),
        (9, "3", "asyncio.wait_for()"),
        (10, "1", "asyncio.wait()"),
        (11, "2", "time.sleep()"),
        (12, "10", "aio.get_running_loop().time() + ..."),
        (13, "5", "time.monotonic() + ..."),
        (14, "1.5", "loop.time() + ..."),
    ]


def test_accepts_named_durations_yields_and_unrelated_calls(
    tmp_path: Path,
) -> None:
    source = (
        "import asyncio\n"
        "import time\n\n"
        "FRAME_TIMEOUT_SECONDS = 10\n\n\n"
        "async def run(work, timeout):\n"
        "    await asyncio.sleep(0)\n"
        "    time.sleep(0)\n"
        "    async with asyncio.timeout(FRAME_TIMEOUT_SECONDS):\n"
        "        await asyncio.wait_for(work(), timeout=None)\n"
        "    await asyncio.wait_for(work(), timeout)\n"
        "    asyncio.timeout_at(100)\n"
        "    return time.monotonic() + timeout, work.time() - 3, len('ab') + 1\n"
    )

    assert _literals(_project(tmp_path, source)) == []


def test_ignores_local_names_shadowing_asyncio(tmp_path: Path) -> None:
    source = (
        "class asyncio:\n"
        "    @staticmethod\n"
        "    def sleep(delay):\n"
        "        return delay\n\n\n"
        "asyncio.sleep(5)\n"
    )

    assert _literals(_project(tmp_path, source)) == []


def test_literals_in_tests_are_not_checked(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_frames.py": "import time\n\n\ndef test_it():\n    time.sleep(1)\n",
        },
    )

    assert _literals(root) == []


def test_literals_are_opt_in(tmp_path: Path) -> None:
    source = "import time\n\ntime.sleep(1)\n"

    assert [
        item for item in violations(_project(tmp_path, source)) if item.code == "MOD010"
    ] == []
    enabled = _project(
        tmp_path / "enabled",
        source,
        config=CONFIG + "rules: {modeling/implicit-duration-unit: warning}\n",
    )
    assert [item.code for item in violations(enabled)].count("MOD010") == 1


def test_reports_names_and_literals_in_source_order(tmp_path: Path) -> None:
    source = (
        "import asyncio\n"
        "_FRAME_TIMEOUT = 2.0\n\n\n"
        "async def settle(page):\n"
        "    await asyncio.sleep(0.5)\n"
    )

    found = violations(_project(tmp_path, source), SELECT)

    assert [(item.line, (item.expected or {})["kind"]) for item in found] == [
        (2, "constant"),
        (6, "literal"),
    ]
