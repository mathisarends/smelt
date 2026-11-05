from pathlib import Path

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

CONFIG = "version: 1\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("SMT333",))
SINGLE_METHOD = (
    "class ReportService:\n    def generate(self, report_id): return report_id\n"
)


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/reports.py": source},
    )


def test_review_keeps_constructor_and_call_arguments_separate(tmp_path: Path) -> None:
    source = """
        class ReportService:
            "A single operation with separately configured dependencies."
            def __init__(self, storage, /, *, timeout: int = 10):
                self._storage = storage
            async def generate(this, report_id: str, /, *parts, refresh=False, **options):
                return await this._load(report_id, *parts, refresh=refresh, **options)
            async def _load(self, *args, **kwargs):
                return await self._storage.load(*args, **kwargs)
    """
    root = _project(tmp_path, source)
    before = (root / "app/reports.py").read_bytes()

    [finding] = violations(root, OPTIONS)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT333",
        "app/reports.py",
        1,
        1,
    )
    assert finding.severity.value == "hint"
    assert finding.expected == {
        "class": "ReportService",
        "method": "generate",
        "candidate_method": "__call__",
        "constructor_signature": "storage, /, *, timeout: int=10",
        "operation_signature": "report_id: str, /, *parts, refresh=False, **options",
        "async_operation": True,
        "review_class_name": True,
        "review_required": True,
        "automatic_rewrite": False,
    }
    assert finding.hint is not None
    assert "class name" in finding.hint
    assert (root / "app/reports.py").read_bytes() == before


@pytest.mark.parametrize(
    ("method", "signature"),
    [
        ("def run(self): pass", ""),
        ("def run(self, value=1, *, flag=True): pass", "value=1, *, flag=True"),
        ("def run(self, /, value, **options): pass", "value, **options"),
        ("def run(self, *, value): pass", "*, value"),
    ],
)
def test_signature_excludes_only_the_instance_receiver(
    tmp_path: Path, method: str, signature: str
) -> None:
    [finding] = violations(
        _project(tmp_path, f"class Operation:\n    {method}\n"), OPTIONS
    )

    assert finding.expected is not None
    assert finding.expected["constructor_signature"] == ""
    assert finding.expected["operation_signature"] == signature
    assert finding.expected["async_operation"] is False


@pytest.mark.parametrize(
    "source",
    [
        "class GenerateReport:\n    def __call__(self, report_id, *, refresh=False): pass\n",
        "class GenerateReport:\n    def __init__(self, storage): pass\n    async def __call__(self, *args, **kwargs): pass\n",
        "class Service:\n    def run(self): pass\n    def __call__(self, value): pass\n",
        "class Service:\n    def run(self): pass\n    def stop(self): pass\n",
        "class Service:\n    def __init__(self, config): pass\n    def _run(self): pass\n",
        "class Empty: pass\n",
        "class Service(Parent):\n    def run(self): pass\n",
        "class Service(object):\n    def run(self): pass\n",
        "class Service(metaclass=Meta):\n    def run(self): pass\n",
        "@dataclass\nclass Service:\n    def run(self): pass\n",
        "class Service:\n    @property\n    def value(self): return 1\n    def run(self): pass\n",
        "class Service:\n    @staticmethod\n    def run(value): pass\n",
        "class Service:\n    @classmethod\n    def create(cls, value): pass\n",
        "class Service:\n    @decorate\n    def run(self): pass\n",
        "class Service:\n    def __enter__(self): return self\n    def run(self): pass\n",
        "class Service:\n    def __iter__(self): return iter(())\n    def run(self): pass\n",
        "class Service:\n    status: str\n    def run(self): pass\n",
        "class Service:\n    run = callback\n    def stop(self): pass\n",
        "class Service:\n    if enabled:\n        def stop(self): pass\n    def run(self): pass\n",
        "class Service:\n    def run(self): pass\n    def run(self, value): pass\n",
        "class Service:\n    def run(*args): pass\n",
    ],
)
def test_callable_and_richer_or_uncertain_apis_remain_valid(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_opt_in_selection_and_closest_package_override(tmp_path: Path) -> None:
    root = _project(tmp_path, SINGLE_METHOD)
    assert violations(root) == []
    assert [v.code for v in violations(root, CheckOptions(select=("SMT33",)))] == [
        "SMT333"
    ]

    write_project(
        root, {"smelt.yml": CONFIG + "rules: {prefer-callable-instance: warning}\n"}
    )
    [finding] = violations(root)
    assert finding.code == "SMT333"
    assert finding.severity.value == "warning"

    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + "structure:\n  contracts:\n    api: {match: app, rules: {prefer-callable-instance: hint}}\n"
        },
    )
    assert [v.code for v in violations(root)] == ["SMT333"]

    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + "rules: {prefer-callable-instance: hint}\n"
            + "structure:\n  contracts:\n    api: {match: app, rules: {prefer-callable-instance: off}}\n"
        },
    )
    assert violations(root, OPTIONS) == []


def test_reasoned_suppression_and_test_files_are_respected(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        SINGLE_METHOD.replace(
            "class ReportService:",
            "class ReportService:  # smelt: ignore[SMT333] -- Keep the published generate API.",
        ),
    )
    write_project(root, {"tests/test_reports.py": SINGLE_METHOD})

    assert violations(root, OPTIONS) == []


def test_cached_review_tracks_method_arguments_and_conversion(tmp_path: Path) -> None:
    root = _project(tmp_path, SINGLE_METHOD)
    assert len(violations(root, OPTIONS)) == 1

    write_project(
        root,
        {
            "app/reports.py": SINGLE_METHOD.replace(
                "report_id):", "report_id, *, refresh=False):"
            )
        },
    )
    cached = check(root, OPTIONS).report
    fresh = check(root, CheckOptions(select=("SMT333",), use_cache=False)).report
    assert cached.violations == fresh.violations
    [finding] = cached.violations
    assert finding.expected is not None
    assert finding.expected["operation_signature"] == "report_id, *, refresh=False"

    write_project(
        root, {"app/reports.py": SINGLE_METHOD.replace("generate", "__call__")}
    )
    assert violations(root, OPTIONS) == []
    assert violations(root, CheckOptions(select=("SMT333",), use_cache=False)) == []
