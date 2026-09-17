from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.cli import main
from tests.engine.test_fix import FEATURE_CONFIG, SOURCES
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def misplaced_test(tmp_path: Path) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": FEATURE_CONFIG,
            **SOURCES,
            "tests/test_calls.py": "from gw.features.voice.domain import calls\n",
        },
    )


class TestDryRun:
    def test_prints_a_rename_diff_and_touches_nothing(
        self,
        capsys: pytest.CaptureFixture[str],
        misplaced_test: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(misplaced_test)

        code, out, _ = _run(capsys, "fix", "--dry-run")

        assert code == 0
        assert "rename from tests/test_calls.py" in out
        assert "rename to tests/voice/test_calls.py" in out
        assert "would apply 1 fix in 1 file" in out
        assert (misplaced_test / "tests/test_calls.py").exists()


class TestApply:
    def test_reports_what_it_changed(
        self,
        capsys: pytest.CaptureFixture[str],
        misplaced_test: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(misplaced_test)

        code, out, _ = _run(capsys, "fix")

        assert code == 0
        assert "SMT401 move tests/test_calls.py to tests/voice/test_calls.py" in out
        assert "applied 1 fix in 1 file" in out
        assert (misplaced_test / "tests/voice/test_calls.py").exists()

    def test_unknown_code_leaves_the_project_alone(
        self,
        capsys: pytest.CaptureFixture[str],
        misplaced_test: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(misplaced_test)

        code, out, _ = _run(capsys, "fix", "SMT9")

        assert code == 0
        assert out == "no fixable violations found\n"
        assert (misplaced_test / "tests/test_calls.py").exists()
