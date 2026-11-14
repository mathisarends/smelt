from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT343",))

ISSUE_EXAMPLE = """\
from collections.abc import AsyncGenerator


class Frames:
    def events(self, *, user_id, run_id) -> AsyncGenerator[int]:
        return self._events(user_id=user_id, run_id=run_id)

    async def _events(self, *, user_id, run_id) -> AsyncGenerator[int]:
        for frame in (user_id, run_id):
            yield frame
"""


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/frames.py": source},
    )


def _class(*methods: str, header: str = "class Frames:") -> str:
    body = "\n".join(
        "\n".join(f"    {line}" for line in method.splitlines()) for method in methods
    )
    return f"{header}\n{body}\n"


FORWARD = "def events(self, run_id):\n    return self._events(run_id)\n"
TARGET = "def _events(self, run_id):\n    return [run_id]\n"


def test_reports_public_method_forwarding_to_private_generator(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, ISSUE_EXAMPLE)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT343",
        "app/frames.py",
        5,
        5,
    )
    assert finding.message == "events only forwards to _events"
    assert finding.expected == {
        "class": "Frames",
        "method": "events",
        "delegate": "_events",
        "delegate_line": 8,
        "automatic_rewrite": False,
    }
    assert finding.hint == "Move the body of _events into events and delete _events."
    assert (root / "app/frames.py").read_text() == ISSUE_EXAMPLE


@pytest.mark.parametrize(
    "methods",
    [
        (FORWARD, TARGET),
        (
            'def events(self, run_id):\n    """Docs."""\n    return self._events(run_id)\n',
            TARGET,
        ),
        ("def events(self, run_id):\n    return self._events(run_id=run_id)\n", TARGET),
        (
            "def events(self, *args, **kwargs):\n    return self._events(*args, **kwargs)\n",
            "def _events(self, *args, **kwargs):\n    return args\n",
        ),
        (
            "def events(this, run_id):\n    return this._events(run_id)\n",
            "def _events(me, run_id):\n    return [run_id]\n",
        ),
        (
            "def events(self):\n    return self.__events()\n",
            "def __events(self):\n    return 1\n",
        ),
    ],
)
def test_reports_forwarding_spellings(tmp_path: Path, methods: tuple[str, str]) -> None:
    [finding] = violations(_project(tmp_path, _class(*methods)), SELECT)
    assert finding.code == "SMT343"


@pytest.mark.parametrize(
    "header",
    [
        "class Frames(object):",
        "from abc import ABC\n\n\nclass Frames(ABC):",
        "from typing import Generic, TypeVar\nT = TypeVar('T')\n\n\nclass Frames(Generic[T]):",
        "class Base:\n    pass\n\n\nclass Frames(Base):",
    ],
)
def test_reports_classes_without_external_bases(tmp_path: Path, header: str) -> None:
    [finding] = violations(
        _project(tmp_path, _class(FORWARD, TARGET, header=header)), SELECT
    )
    assert finding.code == "SMT343"


@pytest.mark.parametrize(
    "methods",
    [
        (
            "def events(self, run_id):\n    if not run_id:\n        raise ValueError(run_id)\n    return self._events(run_id)\n",
            TARGET,
        ),
        (FORWARD, TARGET, "def again(self):\n    return self._events(1)\n"),
        (FORWARD, TARGET, "def name(self):\n    return getattr(self, '_events')\n"),
        (
            "def events(self, run_id):\n    return self._events(run_id, 1)\n",
            "def _events(self, run_id, limit):\n    return limit\n",
        ),
        ("def events(self, run_id):\n    return self._events(str(run_id))\n", TARGET),
        (
            "def events(self, run_id):\n    return self._events(run_id)\n",
            "def _events(self, other):\n    return other\n",
        ),
        (
            "def events(self, run_id):\n    return self._events(run_id)\n",
            "def _events(self, *, run_id):\n    return run_id\n",
        ),
        (
            "@property\ndef events(self):\n    return self._events()\n",
            "def _events(self):\n    return 1\n",
        ),
        (FORWARD, "@staticmethod\ndef _events(run_id):\n    return [run_id]\n"),
        (
            "def __iter__(self):\n    return self._iter()\n",
            "def _iter(self):\n    yield 1\n",
        ),
        ("def events(self, run_id):\n    return self.source._events(run_id)\n", TARGET),
        (
            "def events(self, run_id):\n    return self.events_public(run_id)\n",
            "def events_public(self, run_id):\n    return run_id\n",
        ),
    ],
)
def test_accepts_meaningful_or_shared_delegation(
    tmp_path: Path, methods: tuple[str, ...]
) -> None:
    assert violations(_project(tmp_path, _class(*methods)), SELECT) == []


def test_accepts_external_base_that_may_call_the_hook(tmp_path: Path) -> None:
    source = _class(
        FORWARD, TARGET, header="from app.base import Base\n\n\nclass Frames(Base):"
    )
    assert violations(_project(tmp_path, source), SELECT) == []


def test_accepts_private_method_overridden_in_same_module(tmp_path: Path) -> None:
    source = (
        _class(FORWARD, TARGET) + "\n\n" + _class(TARGET, header="class Live(Frames):")
    )
    assert violations(_project(tmp_path, source), SELECT) == []


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_frames.py": _class(FORWARD, TARGET),
        },
    )
    assert violations(root, SELECT) == []
