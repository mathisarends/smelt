from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

OPTIONS = CheckOptions(select=("SMT314",))
CONFIG = "version: 1\nproject:\n  root_packages:\n  - app\nstructure:\n  contracts:\n    fields:\n      match: app\n      rules:\n        bundled-instance-assignment: error\n"


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yaml": config, "app/__init__.py": "", "app/service.py": source},
    )


@pytest.mark.parametrize(
    "assignment",
    [
        "self.log, self.control = log, control",
        "[self.log, self.control] = [log, control]",
        "self.log = self.control = value",
        "self.log, (self.control, local) = values",
        "self.log, *self.control = values",
        "self.log, self.control = self.control, self.log",
        "self.log, self.control = load_pair()",
    ],
)
def test_bundled_instance_fields_are_reported(tmp_path: Path, assignment: str) -> None:
    root = _project(
        tmp_path,
        "class Inbox:\n    def __init__(self, log, control):\n        "
        + assignment
        + "\n",
    )
    [finding] = violations(root, OPTIONS)
    assert finding.line == 3
    assert finding.column == 9
    assert finding.expected is not None
    assert finding.expected["attributes"] == ["self.log", "self.control"]
    assert finding.expected["automatic_split"] is False


@pytest.mark.parametrize(
    "body",
    [
        "self.log = log\n        self.control = control",
        "log, control = values",
        "self.log, control = values",
        "self.repo.log, self.repo.control = values",
        "def helper(self):\n            self.log, self.control = values",
        "other.log, other.control = values",
    ],
)
def test_local_unpacking_and_other_receivers_are_allowed(
    tmp_path: Path, body: str
) -> None:
    root = _project(
        tmp_path, "class Inbox:\n    def __init__(self):\n        " + body + "\n"
    )
    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize(
    "source",
    [
        "class Inbox:\n    async def update(this, /):\n        this.log, this.control = values",
        "class Inbox:\n    def update(self):\n        if condition:\n            self.log, self.control = values",
        "class Outer:\n    def update(self):\n        class Inner:\n            def __init__(this):\n                this.log, this.control = values",
    ],
)
def test_methods_control_flow_and_nested_class_receivers(
    tmp_path: Path, source: str
) -> None:
    assert len(violations(_project(tmp_path, source), OPTIONS)) == 1


@pytest.mark.parametrize(
    ("decorator", "imports"),
    [
        ("staticmethod", ""),
        ("classmethod", ""),
        ("static", "from builtins import staticmethod as static\n"),
        ("builtins.classmethod", "import builtins\n"),
    ],
)
def test_recognized_static_and_class_methods_are_excluded(
    tmp_path: Path, decorator: str, imports: str
) -> None:
    root = _project(
        tmp_path,
        imports
        + f"class Inbox:\n    @{decorator}\n    def update(self):\n        self.log, self.control = values",
    )
    assert violations(root, OPTIONS) == []


def test_module_function_is_not_an_instance_method(tmp_path: Path) -> None:
    root = _project(tmp_path, "def update(self):\n    self.log, self.control = values")
    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize("name", ["__new__", "__init_subclass__", "__class_getitem__"])
def test_implicit_static_and_class_methods_are_excluded(
    tmp_path: Path, name: str
) -> None:
    root = _project(
        tmp_path, f"class Inbox:\n    def {name}(cls):\n        cls.a, cls.b = values"
    )
    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize(
    "config",
    [
        "version: 1\nproject: {root_packages: [app]}",
        CONFIG.replace(
            "bundled-instance-assignment: error", "bundled-instance-assignment: off"
        ),
    ],
)
def test_assignment_convention_is_opt_in(tmp_path: Path, config: str) -> None:
    root = _project(
        tmp_path,
        "class Inbox:\n    def __init__(self):\n        self.a, self.b = values",
        config,
    )
    assert violations(root) == []


def test_closest_allow_override_wins(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "",
        CONFIG
        + "    exception:\n      match: app.compat\n      rules:\n        bundled-instance-assignment: 'off'\n",
    )
    write_project(
        root,
        {
            "app/compat/__init__.py": "",
            "app/compat/service.py": "class Inbox:\n    def __init__(self):\n        self.a, self.b = values",
        },
    )
    outcome = check(root, OPTIONS)
    assert outcome.report.violations == []
    assert (
        next(
            contract
            for contract in outcome.context.contracts.describe("app.compat.service")
            if contract["package"] == "app.compat"
        )["rules"]["bundled-instance-assignment"]
        == "off"
    )


def test_inline_suppression_is_respected(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "class Inbox:\n    def __init__(self):\n        self.a, self.b = values  # smelt: ignore[SMT314] -- preserve an atomic swap\n",
    )
    outcome = check(root, OPTIONS)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1
