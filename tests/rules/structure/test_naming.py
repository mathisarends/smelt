from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
structure:
  forbidden_names: [utils, helpers]
"""


class TestForbiddenPackageName:
    def test_packages_and_modules(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": CONFIG,
                "app/__init__.py": "",
                "app/utils/__init__.py": "",
                "app/utils/text.py": "",
                "app/billing/__init__.py": "",
                "app/billing/helpers.py": "",
                "app/billing/useful.py": "",
            },
        )

        found = violations(root, CheckOptions(select=("SMT302",)))

        assert codes_at(found) == [
            ("SMT302", "app/billing/helpers.py", None),
            ("SMT302", "app/utils/__init__.py", None),
        ]
        assert found[1].message == 'package name "utils" is not allowed (app.utils)'

    def test_no_names_configured(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": "",
                "app/utils.py": "",
            },
        )

        assert violations(root, CheckOptions(select=("SMT302",))) == []
