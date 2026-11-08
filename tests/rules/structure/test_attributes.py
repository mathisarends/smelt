"""Public state reviews preserve observed consumers and uncertain contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

_CONFIG = """version: 1
project: {root_packages: [app]}
rules: {internal-public-attribute: warning}
"""
_SOURCE = """class Readiness:
    def __init__(self, initial=None, ongoing=None):
        self.initial = initial
        self.ongoing = ongoing
        self.options = None
    async def select(self, context):
        return self.initial if context.stage == 'initial' else self.ongoing
"""


def _project(tmp_path: Path, source: str = _SOURCE) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG,
            "app/__init__.py": "",
            "app/readiness.py": source,
            "app/consumer.py": "def consume(value): return value.options",
        },
    )


def test_internal_state_is_reported_at_its_declaration(tmp_path: Path) -> None:
    findings = violations(_project(tmp_path))
    assert [(v.code, v.path, v.line, v.column) for v in findings] == [
        ("SMT335", "app/readiness.py", 3, 9),
        ("SMT335", "app/readiness.py", 4, 9),
    ]
    first = findings[0]
    assert first.severity == Severity.WARNING
    assert first.source_module == "app.readiness"
    assert first.target_module == "app.readiness.Readiness.initial"
    assert first.expected is not None
    assert first.expected["attribute"] == "initial"
    assert first.expected["class"] == "Readiness"
    assert first.expected["kind"] == "instance-attribute"
    assert first.expected["suggested_name"] == "_initial"
    assert first.expected["internal_references"] == [3, 7]
    assert first.expected["automatic_rename"] is False
    assert (
        "external consumers and arbitrary dynamic access"
        in first.expected["usage_scope"]["unassessed_consumers"]
    )


@pytest.mark.parametrize(
    "declaration", ["initial = 1", "initial: int = 1", "initial: int"]
)
def test_class_data_declarations_are_reviewed(tmp_path: Path, declaration: str) -> None:
    [finding] = violations(_project(tmp_path, f"class Readiness:\n    {declaration}"))
    assert (finding.line, finding.column) == (2, 5)
    assert finding.expected is not None
    assert finding.expected["kind"] == "class-attribute"


@pytest.mark.parametrize(
    "consumer",
    [
        "def consume(value): return value.initial",
        "def consume(value): value.initial = 1",
        "def consume(value): del value.initial",
        "def consume(value): return getattr(value, 'initial')",
        "def consume(value): return hasattr(value, 'initial')",
        "def consume(value): setattr(value, 'initial', 1)",
        "def consume(value): delattr(value, 'initial')",
        "class Other:\n    def read(self): return self.initial",
        "from .readiness import Readiness as R\nvalue = R().initial",
    ],
)
@pytest.mark.parametrize("path", ["app/other.py", "tests/test_readiness.py"])
def test_any_external_access_preserves_visibility(
    tmp_path: Path, consumer: str, path: str
) -> None:
    root = _project(
        tmp_path, "class Readiness:\n    def __init__(self): self.initial = 1"
    )
    write_project(root, {path: consumer})
    assert violations(root) == []


def test_external_access_in_the_defining_module_is_preserved(tmp_path: Path) -> None:
    root = _project(
        tmp_path, "class Readiness:\n    initial = 1\nvalue = Readiness.initial"
    )
    assert violations(root) == []


@pytest.mark.parametrize(
    "source",
    [
        "@dataclass\nclass Readiness:\n    initial: int = 1",
        "@attrs.define\nclass Readiness:\n    initial: int = 1",
        "class Readiness(BaseModel):\n    initial: int = 1",
        "class Readiness(NamedTuple):\n    initial: int",
        "class Readiness(TypedDict):\n    initial: int",
        "class Readiness(Plugin):\n    def __init__(self): self.initial = 1",
        "class Readiness:\n    initial = 1\nclass Child(Readiness): pass",
        "class Readiness(metaclass=Meta):\n    initial = 1",
        "class Readiness:\n    __slots__ = ('initial',)\n    def __init__(self): self.initial = 1",
        "class Readiness:\n    def __getattr__(self, name): return 1\n    initial = 1",
        "class Readiness:\n    @property\n    def initial(self): return 1",
        "class Readiness:\n    initial = descriptor()",
        "class Readiness:\n    initial = descriptor\n    def __init__(self): self.initial = 1",
        "class Readiness:\n    def __init__(self): self._initial = 1",
        "class Readiness:\n    def __init__(self): self.__initial__ = 1",
        "class Readiness:\n    @staticmethod\n    def create(other): other.initial = 1",
        "class Readiness:\n    def run(self):\n        self = other\n        self.initial = 1",
        "class Readiness:\n    initial = 1\nReadiness = replacement",
    ],
)
def test_uncertain_contracts_and_non_fields_are_preserved(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source)) == []


@pytest.mark.parametrize(
    "facade",
    [
        "from .readiness import Readiness",
        "from .aliases import PublicReadiness as Exported\n__all__ = ['Exported']",
    ],
)
def test_public_facades_preserve_data_contracts(tmp_path: Path, facade: str) -> None:
    root = _project(tmp_path)
    write_project(
        root,
        {
            "app/__init__.py": facade,
            "app/aliases.py": "from .readiness import Readiness as PublicReadiness",
        },
    )
    assert violations(root) == []


def test_explicit_exports_preserve_data_contracts(tmp_path: Path) -> None:
    assert violations(_project(tmp_path, "__all__ = ['Readiness']\n" + _SOURCE)) == []


def test_repeated_writes_custom_receiver_and_name_collisions(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "class Readiness:\n    def __init__(this, /):\n        this.initial: int = 1\n        this._initial = 0\n        this._initial_internal = 0\n    def reset(this): this.initial = 2",
    )
    [finding] = violations(root)
    assert finding.line == 3
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_initial_internal_2"
    assert finding.expected["internal_references"] == [3, 6]
    write_project(
        root,
        {
            "app/readiness.py": "class Readiness:\n    def __init__(self): self._initial = 1"
        },
    )
    assert violations(root) == []


def test_test_only_attributes_are_owned_by_smt430(tmp_path: Path) -> None:
    root = _project(tmp_path, "class Readiness:\n    initial = 1")
    write_project(
        root,
        {
            "smelt.yml": _CONFIG.replace(
                "internal-public-attribute: warning",
                "internal-public-attribute: warning, test-only-public-attribute: warning",
            ),
            "tests/test_readiness.py": "from app.readiness import Readiness\ndef test_initial(): assert Readiness().initial == 1",
        },
    )
    assert [v.code for v in violations(root)] == ["SMT430"]
    write_project(
        root, {"smelt.yml": _CONFIG + "tests: {quality_exclude: ['tests/**']}\n"}
    )
    assert violations(root) == []


@pytest.mark.parametrize("selection", ["SMT335", "SMT33", "internal-public-attribute"])
def test_rule_is_opt_in_and_supports_explicit_selection(
    tmp_path: Path, selection: str
) -> None:
    root = _project(tmp_path, "class Readiness:\n    initial = 1")
    write_project(
        root,
        {
            "smelt.yml": _CONFIG.replace(
                "rules: {internal-public-attribute: warning}", ""
            )
        },
    )
    assert violations(root) == []
    assert [v.code for v in violations(root, CheckOptions(select=(selection,)))] == [
        "SMT335"
    ]


def test_package_override_ignore_and_reasoned_suppression(tmp_path: Path) -> None:
    source = "class Readiness:\n    initial = 1"
    root = _project(tmp_path, source)
    assert violations(root, CheckOptions(ignore=("SMT335",))) == []
    write_project(
        root,
        {
            "smelt.yml": _CONFIG
            + "structure:\n  contracts:\n    api:\n      match: app\n      rules: {internal-public-attribute: off}\n"
        },
    )
    assert violations(root, CheckOptions(select=("SMT335",))) == []
    write_project(
        root,
        {
            "smelt.yml": _CONFIG.replace("warning", "error"),
            "app/readiness.py": source
            + "  # smelt: ignore[SMT335] -- public diagnostic API\n",
        },
    )
    outcome = check(root)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1
    write_project(root, {"app/readiness.py": source})
    assert [v.severity for v in violations(root)] == [Severity.ERROR]


def test_cached_results_follow_external_uses_exports_and_inheritance(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "class Readiness:\n    initial = 1")
    states = [
        {"app/other.py": "def consume(value): return value.initial"},
        {
            "app/other.py": "",
            "tests/test_readiness.py": "def test_initial(value): assert value.initial == 1",
        },
        {
            "tests/test_readiness.py": "",
            "app/__init__.py": "from .readiness import Readiness",
        },
        {
            "app/__init__.py": "",
            "app/other.py": "from .readiness import Readiness as R\nclass Child(R): pass",
        },
        {"app/other.py": ""},
    ]
    assert [v.code for v in violations(root)] == ["SMT335"]
    for index, files in enumerate(states):
        write_project(root, files)
        cached = violations(root)
        assert cached == violations(root, CheckOptions(use_cache=False))
        assert [v.code for v in cached] == (
            ["SMT335"] if index == len(states) - 1 else []
        )
