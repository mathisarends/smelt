from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.mirror_inference import (
    DEFAULT_MIRROR,
    ROOT_MIRROR,
    MirrorGuess,
    clashing_test_roots,
    infer_mirror,
)
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path

SOURCES = {
    "app/__init__.py": "",
    "app/billing/__init__.py": "",
    "app/billing/invoice.py": "",
}


class TestInferMirror:
    def test_default_pattern(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/billing/test_invoice.py": "",
                "tests/billing/test_billing.py": "",
                "tests/test_misc.py": "",
            },
        )

        guess = infer_mirror(tmp_path, ["tests"], [tmp_path / "app"])

        assert guess == MirrorGuess(
            DEFAULT_MIRROR, mirrored=2, total=3, name_clashes=("tests",)
        )

    def test_root_pattern(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/__init__.py": "",
                "tests/app/billing/test_invoice.py": "",
            },
        )

        guess = infer_mirror(tmp_path, ["tests"], [tmp_path / "app"])

        assert guess == MirrorGuess(ROOT_MIRROR, mirrored=1, total=1)

    def test_no_mirrored_test(self, tmp_path: Path) -> None:
        write_project(tmp_path, {**SOURCES, "tests/test_scenario.py": ""})

        assert infer_mirror(tmp_path, ["tests"], [tmp_path / "app"]) is None


class TestClashingTestRoots:
    def test_without_packages_or_importlib(self, tmp_path: Path) -> None:
        write_project(tmp_path, {"tests/billing/test_invoice.py": ""})

        assert clashing_test_roots(tmp_path, ["tests"]) == ("tests",)

    def test_test_packages_tell_files_apart(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {"tests/__init__.py": "", "tests/billing/test_invoice.py": ""},
        )

        assert clashing_test_roots(tmp_path, ["tests"]) == ()

    def test_importlib_in_the_member_pyproject(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                "backend/pyproject.toml": (
                    '[tool.pytest.ini_options]\naddopts = ["--import-mode=importlib"]\n'
                ),
                "backend/tests/users/test_router.py": "",
                "libs/agent/tests/test_tools.py": "",
            },
        )

        roots = ["backend/tests", "libs/agent/tests"]

        assert clashing_test_roots(tmp_path, roots) == ("libs/agent/tests",)
