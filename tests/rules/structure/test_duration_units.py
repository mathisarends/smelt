from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT357",))


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/frames.py": source},
    )


def _names(root: Path) -> list[tuple[int | None, str, str]]:
    return [
        (item.line, item.expected["kind"], item.expected["name"])
        for item in violations(root, SELECT)
        if item.expected is not None
    ]


def test_reports_unitless_constant_from_the_issue(tmp_path: Path) -> None:
    (finding,) = violations(_project(tmp_path, "_FRAME_TIMEOUT = 2.0\n"), SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT357",
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


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_frames.py": "TIMEOUT = 5\n",
        },
    )

    assert _names(root) == []


def test_is_opt_in(tmp_path: Path) -> None:
    source = "_TIMEOUT = 5\n"

    assert [
        item for item in violations(_project(tmp_path, source)) if item.code == "SMT357"
    ] == []
    enabled = _project(
        tmp_path / "enabled",
        source,
        config=CONFIG + "rules: {missing-duration-unit: warning}\n",
    )
    assert [item.code for item in violations(enabled)].count("SMT357") == 1
