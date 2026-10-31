from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """version: 1
project: {root_packages: [app]}
rules: {test-only-public-attribute: warning}
"""
SOURCE = """class Worker:
    def __init__(self):
        self.pending = []
    def run(self):
        self.pending.clear()
"""
TEST = """from app.worker import Worker
def test_run():
    worker = Worker()
    worker.run()
    assert worker.pending == []
"""


def _project(
    tmp_path: Path, source: str = SOURCE, test: str = TEST, config: str = CONFIG
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "app/worker.py": source,
            "tests/test_worker.py": test,
        },
    )


def test_public_instance_state_is_not_justified_by_a_test_read(tmp_path: Path) -> None:
    [finding] = violations(_project(tmp_path))
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT430",
        "app/worker.py",
        3,
        9,
    )
    assert finding.severity == Severity.WARNING
    assert finding.source_module == "app.worker"
    assert finding.target_module == "app.worker.Worker.pending"
    assert finding.expected == {
        "class": "Worker",
        "attribute": "pending",
        "kind": "instance-attribute",
        "suggested_name": "_pending",
        "internal_references": [3, 5],
        "test_references": [{"path": "tests/test_worker.py", "line": 5}],
        "automatic_rename": False,
        "usage_scope": {
            "root_packages": ["app"],
            "source_roots": ["."],
            "test_roots": ["tests"],
            "excluded_paths": [],
            "unassessed_consumers": [
                "excluded files (including maintainer scripts)",
                "files outside selected packages and roots",
                "external consumers and arbitrary dynamic access",
            ],
        },
    }


@pytest.mark.parametrize(
    "test",
    [
        "from app.worker import Worker as W\ndef test_run(): assert W().pending == []",
        "import app.worker as w\ndef test_run(): assert w.Worker().pending == []",
        "from app.worker import Worker\ndef test_run(worker: Worker): assert worker.pending == []",
        "from app.worker import Worker\ndef test_run(worker: 'Worker'): assert worker.pending == []",
        "import pytest\nfrom app.worker import Worker\n@pytest.fixture\ndef worker(): return Worker()\ndef test_run(worker): assert worker.pending == []",
        "from app.worker import Worker\ndef test_run():\n    worker = Worker()\n    alias = worker\n    assert alias.pending == []",
    ],
)
def test_known_constructor_annotation_fixture_and_alias_reads(
    tmp_path: Path, test: str
) -> None:
    [finding] = violations(_project(tmp_path, test=test))
    assert finding.expected is not None
    assert finding.expected["attribute"] == "pending"


def test_facade_and_conftest_fixture_provenance_are_resolved(tmp_path: Path) -> None:
    root = _project(tmp_path, test="def test_run(worker): assert worker.pending == []")
    write_project(
        root,
        {
            "app/__init__.py": "from .worker import Worker as PublicWorker",
            "tests/conftest.py": "import pytest\nfrom app import PublicWorker as W\n@pytest.fixture\ndef worker(): yield W()",
        },
    )
    [finding] = violations(root)
    assert finding.target_module == "app.worker.Worker.pending"


@pytest.mark.parametrize(
    "declaration", ["pending = None", "pending: int = 0", "pending: int"]
)
def test_simple_class_attributes(tmp_path: Path, declaration: str) -> None:
    [finding] = violations(
        _project(
            tmp_path,
            source=f"class Worker:\n    {declaration}",
            test="from app.worker import Worker as W\ndef test_run(): assert W.pending is not None",
        )
    )
    assert finding.line == 2
    assert finding.expected is not None
    assert finding.expected["kind"] == "class-attribute"


@pytest.mark.parametrize(
    "consumer",
    [
        "from app.worker import Worker\nvalue = Worker().pending",
        "def consume(worker): return worker.pending",
        "def consume(worker): worker.pending = []",
        "def consume(worker): del worker.pending",
        "def consume(worker): return getattr(worker, 'pending')",
        "def consume(worker): return hasattr(worker, 'pending')",
        "def consume(worker): setattr(worker, 'pending', [])",
        "class Other:\n    def run(self): return self.pending",
    ],
)
def test_possible_production_access_preserves_public_attributes(
    tmp_path: Path, consumer: str
) -> None:
    root = _project(tmp_path)
    write_project(root, {"app/consumer.py": consumer})
    assert violations(root) == []


def test_child_object_reads_do_not_count_as_reads_on_the_parent(tmp_path: Path) -> None:
    [finding] = violations(
        _project(
            tmp_path,
            source="class Worker:\n    def __init__(self):\n        self.pending = queue()\n        self.count = 0",
            test="from app.worker import Worker\ndef test_run(): assert Worker().pending.count == 0",
        )
    )
    assert finding.expected is not None
    assert finding.expected["attribute"] == "pending"


def test_production_access_outside_the_class_in_its_own_module_preserves_it(
    tmp_path: Path,
) -> None:
    assert (
        violations(_project(tmp_path, source=SOURCE + "value = Worker().pending")) == []
    )


@pytest.mark.parametrize(
    "test",
    [
        "class Fake:\n    pending = []\ndef test_run(): assert Fake().pending == []",
        "from app.worker import Worker\ndef test_run(Worker): assert Worker().pending == []",
        "from app.worker import Worker\ndef test_run():\n    worker = Worker()\n    worker = fake\n    assert worker.pending == []",
        "def test_run(worker): assert worker.pending == []",
        "from app.worker import Worker\ndef test_run(): Worker().run()",
        "from app.worker import Worker\ndef test_run(): Worker().pending = []",
        "from app.worker import Worker\ndef test_run(): assert unknown_factory().pending == []",
    ],
)
def test_unknown_shadowed_fake_and_write_only_test_receivers_are_excluded(
    tmp_path: Path, test: str
) -> None:
    assert violations(_project(tmp_path, test=test)) == []


@pytest.mark.parametrize(
    "source",
    [
        "@dataclass\nclass Worker:\n    pending: int = 0",
        "class Worker(Base):\n    def __init__(self): self.pending = []",
        "class Worker:\n    def __init__(self): self.pending = []\nclass Child(Worker): pass",
        "class Worker(metaclass=Meta):\n    pending = 0",
        "class Worker:\n    __slots__ = ('pending',)\n    def __init__(self): self.pending = []",
        "class Worker:\n    def __getattr__(self, name): return []\n    def __init__(self): self.pending = []",
        "class Worker:\n    @property\n    def pending(self): return []",
        "class Worker:\n    pending = descriptor()",
        "class Worker:\n    pending = descriptor\n    def __init__(self): self.pending = []",
        "class Worker:\n    @staticmethod\n    def build(other): other.pending = []",
        "class Worker:\n    def build(self):\n        def nested(other): other.pending = []",
        "class Worker:\n    def build(self):\n        self = other\n        self.pending = []",
        "class Worker:\n    def build(self):\n        def nested(self): self.pending = []",
        "class Worker:\n    def pending(self): return []",
        "class Worker:\n    def __init__(self): self._pending = []",
        "class Worker:\n    def __init__(self): self.pending = []\nWorker = replacement",
    ],
)
def test_uncertain_class_contracts_and_non_fields_are_preserved(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source=source)) == []


def test_annotated_field_custom_receiver_and_existing_private_name(
    tmp_path: Path,
) -> None:
    [finding] = violations(
        _project(
            tmp_path,
            source="class Worker:\n    def __init__(this, /):\n        this.pending: list = []\n        this._pending = []\n        this._pending_internal = []",
        )
    )
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_pending_internal_2"


def test_multiple_writes_and_tests_produce_one_finding_with_sorted_evidence(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path, source=SOURCE + "    def reset(self): self.pending = []\n"
    )
    write_project(
        root,
        {
            "tests/test_a.py": "from app.worker import Worker\ndef test_state(): assert Worker().pending == []"
        },
    )
    [finding] = violations(root)
    assert finding.line == 3
    assert finding.expected is not None
    assert finding.expected["test_references"] == [
        {"path": "tests/test_a.py", "line": 2},
        {"path": "tests/test_worker.py", "line": 5},
    ]


@pytest.mark.parametrize(
    ("policy", "options"),
    [
        ("", CheckOptions()),
        (
            "rules: {SMT430: off}",
            CheckOptions(select=("ALL",)),
        ),
        (
            "rules: {test-only-public-attribute: off}",
            CheckOptions(),
        ),
        (
            "rules: {SMT430: warning}",
            CheckOptions(ignore=("SMT430",)),
        ),
        (
            "rules: {SMT430: warning}\ntests: {quality_exclude: ['tests/**']}",
            CheckOptions(),
        ),
    ],
)
def test_rule_selection_and_test_quality_exclusions(
    tmp_path: Path, policy: str, options: CheckOptions
) -> None:
    root = _project(
        tmp_path, config=f"version: 1\nproject: {{root_packages: [app]}}\n{policy}"
    )
    assert not any(finding.code == "SMT430" for finding in violations(root, options))


@pytest.mark.parametrize("selection", ["SMT430", "SMT43"])
def test_explicit_code_and_prefix_selection(tmp_path: Path, selection: str) -> None:
    root = _project(tmp_path, config="version: 1\nproject: {root_packages: [app]}\n")
    [finding] = violations(root, CheckOptions(select=(selection,)))
    assert finding.code == "SMT430"


def test_related_public_use_rules_share_evidence_without_duplicate_findings(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        source="""def helper(): return 1
class Worker:
    def __init__(self): self.pending = []
    def emit(self): return helper()
    @staticmethod
    def normalize(value): return value
    def run(self):
        self.pending.append(self.emit())
        return self.normalize(len(self.pending))
""",
        test="""from app.worker import Worker, helper
def test_run():
    worker = Worker()
    assert worker.run() == 1
    assert worker.pending == [1]
    assert helper() == 1
""",
        config="version: 1\nproject: {root_packages: [app]}\nrules: {SMT316: warning, SMT317: hint, SMT429: warning, SMT430: warning}\n",
    )
    assert sorted(finding.code for finding in violations(root)) == [
        "SMT316",
        "SMT317",
        "SMT429",
        "SMT430",
    ]


def test_reasoned_suppression_and_severity_override(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        source=SOURCE.replace(
            "self.pending = []",
            "self.pending = []  # smelt: ignore[SMT430] -- supported diagnostic API",
        ),
        config=CONFIG.replace("warning", "error"),
    )
    outcome = check(root)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1
    write_project(root, {"app/worker.py": SOURCE})
    [finding] = violations(root)
    assert finding.severity == Severity.ERROR


def test_custom_roots_and_private_state_observed_through_public_behavior(
    tmp_path: Path,
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG.replace(
                "root_packages: [app]",
                "root_packages: [app], source_roots: [src], test_roots: [spec]",
            ),
            "src/app/__init__.py": "",
            "src/app/worker.py": SOURCE,
            "spec/test_worker.py": TEST,
        },
    )
    [finding] = violations(root)
    assert finding.path == "src/app/worker.py"
    write_project(
        root,
        {
            "src/app/worker.py": SOURCE.replace("pending", "_pending"),
            "spec/test_worker.py": "from app.worker import Worker\ndef test_run(): assert Worker().run() is None",
        },
    )
    assert violations(root) == []
