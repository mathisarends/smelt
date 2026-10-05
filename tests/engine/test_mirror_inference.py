from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.mirror_inference import (
    DEFAULT_MIRROR,
    ROOT_MIRROR,
    MirrorGuess,
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

        assert guess == MirrorGuess(DEFAULT_MIRROR, mirrored=2, total=3)

    def test_root_pattern(self, tmp_path: Path) -> None:
        write_project(tmp_path, {**SOURCES, "tests/app/billing/test_invoice.py": ""})

        guess = infer_mirror(tmp_path, ["tests"], [tmp_path / "app"])

        assert guess == MirrorGuess(ROOT_MIRROR, mirrored=1, total=1)

    def test_no_mirrored_test(self, tmp_path: Path) -> None:
        write_project(tmp_path, {**SOURCES, "tests/test_scenario.py": ""})

        assert infer_mirror(tmp_path, ["tests"], [tmp_path / "app"]) is None
