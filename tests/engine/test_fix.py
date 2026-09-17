from typing import TYPE_CHECKING

from smelt.config import load_config
from smelt.engine.fix import FixOutcome, run_fix
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

FEATURE_CONFIG = """
version: 1
project:
  root_packages: [gw]
architecture:
  features:
    root: gw.features
  layers:
    domain: {path: domain}
    infra: {path: infra}
tests:
  layout: feature
  pattern: "tests/{feature}"
"""

SOURCES = {
    "gw/__init__.py": "",
    "gw/features/__init__.py": "",
    "gw/features/voice/__init__.py": "",
    "gw/features/voice/domain/__init__.py": "",
    "gw/features/voice/domain/calls.py": "",
    "gw/features/voice/infra/__init__.py": "",
}


def _fix(root: Path, *codes: str, dry_run: bool = False) -> FixOutcome:
    return run_fix(load_config(root / "smelt.yaml"), codes, dry_run=dry_run)


class TestMoveTestFile:
    def test_file_is_moved_and_violation_is_gone(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "tests/test_calls.py": "from gw.features.voice.domain import calls\n",
            },
        )

        outcome = _fix(root, "SMT401")

        assert [v.code for v in outcome.applied] == ["SMT401"]
        assert not (root / "tests/test_calls.py").exists()
        assert (root / "tests/voice/test_calls.py").read_text(encoding="utf-8") == (
            "from gw.features.voice.domain import calls\n"
        )
        assert [v.code for v in violations(root)] == []

    def test_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "tests/test_calls.py": "from gw.features.voice.domain import calls\n",
            },
        )

        outcome = _fix(root, "SMT401", dry_run=True)

        assert (root / "tests/test_calls.py").exists()
        assert not (root / "tests/voice/test_calls.py").exists()
        [diff] = outcome.diffs
        assert diff.path == "tests/voice/test_calls.py"
        assert diff.renamed_from == "tests/test_calls.py"


class TestRemoveUnusedSuppression:
    def test_whole_comment_is_removed(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "gw/features/voice/infra/client.py": "import os  # smelt: ignore[SMT101] -- old\n",
            },
        )

        outcome = _fix(root, "SMT901")

        assert [v.code for v in outcome.applied] == ["SMT901"]
        assert (root / "gw/features/voice/infra/client.py").read_text(
            encoding="utf-8"
        ) == "import os\n"
        assert [v.code for v in violations(root)] == []

    def test_only_selected_codes_are_fixed(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": FEATURE_CONFIG,
                **SOURCES,
                "gw/features/voice/infra/client.py": "import os  # smelt: ignore[SMT101] -- old\n",
                "tests/test_calls.py": "from gw.features.voice.domain import calls\n",
            },
        )

        outcome = _fix(root, "SMT401")

        assert [v.code for v in outcome.applied] == ["SMT401"]
        assert "smelt: ignore" in (
            root / "gw/features/voice/infra/client.py"
        ).read_text(encoding="utf-8")


class TestNothingToDo:
    def test_clean_project_reports_no_fixes(self, tmp_path: Path) -> None:
        root = write_project(tmp_path, {"smelt.yaml": FEATURE_CONFIG, **SOURCES})

        outcome = _fix(root)

        assert outcome.applied == []
        assert outcome.diffs == []
