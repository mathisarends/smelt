from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT348",))
HEADER = (
    "from abc import ABC, abstractmethod\n"
    "from typing import Protocol\n"
    "from uuid import UUID\n\n\n"
)


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/ports.py": source},
    )


def test_reports_undocumented_abstract_property_from_the_issue(
    tmp_path: Path,
) -> None:
    source = HEADER + (
        "class BrowserAgentProfile(ABC):\n"
        '    """Borrow the existing account profile."""\n\n'
        "    @property\n"
        "    @abstractmethod\n"
        "    def initial_tab_id(self) -> UUID: ...\n\n"
        "    @abstractmethod\n"
        "    async def revoke(self) -> None:\n"
        '        """Fence native access and settle all resource leases."""\n'
    )
    root = _project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT348",
        "app/ports.py",
        11,
        5,
    )
    assert finding.message == (
        "Abstract method BrowserAgentProfile.initial_tab_id has a placeholder "
        "body and no docstring"
    )
    assert finding.expected == {
        "class": "BrowserAgentProfile",
        "method": "initial_tab_id",
        "interface": "abstract",
        "automatic_rewrite": False,
    }
    assert finding.hint == (
        "Replace the body of BrowserAgentProfile.initial_tab_id with a docstring "
        "that states its contract."
    )
    assert (root / "app/ports.py").read_text() == source


@pytest.mark.parametrize(
    ("source", "method", "interface"),
    [
        (
            "class Port(ABC):\n    @abstractmethod\n    def load(self) -> None:\n"
            "        pass\n",
            "load",
            "abstract",
        ),
        (
            "class Port(ABC):\n    @abstractmethod\n    def load(self) -> None:\n"
            "        raise NotImplementedError\n",
            "load",
            "abstract",
        ),
        (
            "import abc\n\nclass Port(abc.ABC):\n    @abc.abstractmethod\n"
            '    def load(self) -> None:\n        raise NotImplementedError("x")\n',
            "load",
            "abstract",
        ),
        (
            "class Clock(Protocol):\n    def now(self) -> float: ...\n",
            "now",
            "protocol",
        ),
        (
            "import typing\n\nclass Clock[T](typing.Protocol[T]):\n"
            "    async def now(self) -> T: ...\n",
            "now",
            "protocol",
        ),
        (
            "class Handler(Protocol):\n    def __call__(self, event: str) -> None: ...\n",
            "__call__",
            "protocol",
        ),
    ],
)
def test_reports_placeholder_bodies(
    tmp_path: Path, source: str, method: str, interface: str
) -> None:
    [finding] = violations(_project(tmp_path, HEADER + source), SELECT)
    assert finding.expected is not None
    assert (finding.expected["method"], finding.expected["interface"]) == (
        method,
        interface,
    )


@pytest.mark.parametrize(
    "source",
    [
        # A docstring states the contract; a trailing ... is Ruff PIE790's concern.
        "class Port(ABC):\n    @abstractmethod\n    def load(self) -> None:\n"
        '        """Load the state."""\n        ...\n',
        # A default implementation documents behavior in code.
        "class Port(ABC):\n    @abstractmethod\n    def load(self) -> int:\n"
        "        return 0\n",
        "class Clock(Protocol):\n    def now(self) -> float:\n        return 0.0\n",
        # Concrete classes and non-abstract members are not interfaces.
        "class Service:\n    def load(self) -> None: ...\n",
        "class Port(ABC):\n    def load(self) -> None: ...\n",
        # Signatures dictated elsewhere.
        "from typing import overload\n\nclass Clock(Protocol):\n"
        "    @overload\n    def at(self, x: int) -> int: ...\n"
        "    @overload\n    def at(self, x: str) -> str: ...\n"
        '    def at(self, x: int | str) -> int | str:\n        """Look up x."""\n',
        "from typing import override\n\nclass Child(Port):\n"
        "    @override\n    @abstractmethod\n    def load(self) -> None: ...\n",
        "class Clock(Protocol):\n    def __enter__(self) -> Clock: ...\n",
        "class Clock(Protocol):\n    @property\n    def zone(self) -> str:\n"
        '        """The IANA zone."""\n\n'
        "    @zone.setter\n    def zone(self, value: str) -> None: ...\n",
        # Protocol attributes have no body.
        "class Clock(Protocol):\n    zone: str\n",
    ],
)
def test_accepts_documented_implemented_and_dictated_members(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, HEADER + source), SELECT) == []


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_ports.py": HEADER
            + "class Fake(Protocol):\n    def load(self) -> None: ...\n",
        },
    )
    assert violations(root, SELECT) == []


def test_rule_is_opt_in_and_uses_configured_severity(tmp_path: Path) -> None:
    source = HEADER + "class Clock(Protocol):\n    def now(self) -> float: ...\n"
    assert violations(_project(tmp_path / "default", source)) == []
    config = CONFIG + "rules: {missing-interface-docstring: warning}\n"
    [finding] = violations(_project(tmp_path / "enabled", source, config=config))
    assert (finding.code, finding.severity) == ("SMT348", Severity.WARNING)
