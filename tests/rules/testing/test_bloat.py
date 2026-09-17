from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, ChangeSet, FileChange
from smelt.config import load_config
from smelt.engine.check import CheckOptions, run_check
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

CONFIG = """
version: 1
project:
  root_packages: [app]
tests:
  bloat:
    ratio: 5
    min_test_loc: 20
"""


def _run(
    root: Path, changes: dict[str, tuple[int, int]], *, changed: bool = True
) -> list[Violation]:
    loaded = load_config(root / "smelt.yaml")
    change_set = ChangeSet(
        {
            path: FileChange(path, added, deleted)
            for path, (added, deleted) in changes.items()
        }
    )
    context = AnalysisContext(loaded.root, loaded.config, changes=change_set)
    options = CheckOptions(select=("SMT407",), changed=changed)
    return run_check(loaded, options, context=context).report.violations


def _project(tmp_path: Path) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": CONFIG,
            "app/__init__.py": "",
            "app/service.py": "x = 1\n",
            "tests/test_service.py": (
                "from unittest.mock import Mock, patch\n\n\n"
                '@patch("requests.get")\n'
                "def test_a(get) -> None:\n"
                "    assert Mock()\n"
            ),
            "tests/test_other.py": "def test_b() -> None:\n    assert True\n",
        },
    )


class TestBloatedTestChange:
    def test_reports_ratio_with_summary(self, tmp_path: Path) -> None:
        root = _project(tmp_path)

        [found] = _run(
            root,
            {
                "app/service.py": (2, 1),
                "tests/test_service.py": (30, 0),
                "tests/test_other.py": (10, 0),
            },
        )

        assert found.path == "tests/test_service.py"
        assert found.message == (
            "40 test lines added for 3 production lines changed "
            "(ratio 13.3, max 5; 1 mocks, 1 patches, 2 assertions)"
        )

    def test_small_or_proportional_changes_pass(self, tmp_path: Path) -> None:
        root = _project(tmp_path)

        assert (
            _run(root, {"app/service.py": (1, 0), "tests/test_service.py": (19, 0)})
            == []
        )
        assert (
            _run(root, {"app/service.py": (10, 0), "tests/test_service.py": (40, 0)})
            == []
        )
        assert _run(root, {"tests/test_service.py": (400, 0)}) == []

    def test_only_runs_in_changed_mode(self, tmp_path: Path) -> None:
        root = _project(tmp_path)

        found = _run(
            root,
            {"app/service.py": (1, 0), "tests/test_service.py": (100, 0)},
            changed=False,
        )

        assert found == []
