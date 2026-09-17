from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
architecture:
  composition_root: [app.bootstrap]
  di_frameworks: [dishka]
  layers:
    application:
      path: application
      third_party: {default: allow, deny: [dishka]}
"""


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    base = {
        "smelt.yaml": CONFIG,
        "app/__init__.py": "",
        "app/application/__init__.py": "",
        "app/bootstrap.py": "import dishka\nfrom app.application import service\n",
    }
    return write_project(tmp_path, {**base, **files})


class TestCompositionRootLeak:
    def test_framework_inside_composition_root_is_fine(self, tmp_path: Path) -> None:
        root = _project(tmp_path, {"app/application/service.py": ""})

        assert violations(root, CheckOptions(select=("SMT1",))) == []

    def test_framework_outside_root_wins_over_third_party_rule(
        self, tmp_path: Path
    ) -> None:
        root = _project(
            tmp_path, {"app/application/service.py": "from dishka import FromDishka\n"}
        )

        [found] = violations(root, CheckOptions(select=("SMT1",)))

        assert found.code == "SMT106"
        assert (
            found.message
            == "dishka may only be used in the composition root (app.bootstrap)"
        )

    def test_importing_the_composition_root(self, tmp_path: Path) -> None:
        root = _project(
            tmp_path, {"app/application/service.py": "from app import bootstrap\n"}
        )

        found = violations(root, CheckOptions(select=("SMT106",)))

        assert [v.message for v in found] == [
            "app.application.service must not import the composition root app.bootstrap"
        ]
