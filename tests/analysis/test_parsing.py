from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.parsing import AnalysisError, python_note
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

RUNNING = f"{sys.version_info[0]}.{sys.version_info[1]}"

# Valid only on newer Pythons; each file also breaks a layer boundary.
PY313 = "from app.infra import db\n\n\ndef first[T = int](items: list[T]) -> T:\n    return items[0]\n"
PY314 = (
    "from app.infra import db\n\n\n"
    "def run() -> None:\n"
    "    try:\n"
    "        db.connect()\n"
    "    except ValueError, TypeError:\n"
    "        pass\n"
    "    template = t'{db}'\n"
)


def _project(tmp_path: Path, service: str, requires: str = ">=3.12") -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": LAYERED_CONFIG,
            "pyproject.toml": f'[project]\nname = "app"\nrequires-python = "{requires}"\n',
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/domain/service.py": service,
            "app/application/__init__.py": "",
            "app/infra/__init__.py": "",
            "app/infra/db.py": "",
        },
    )


class TestPythonNote:
    def test_names_the_running_python(self, tmp_path: Path) -> None:
        assert python_note(tmp_path) == f" (parsed by Python {RUNNING})"

    def test_suggests_the_targeted_python(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.99"\n', encoding="utf-8"
        )

        assert python_note(tmp_path) == (
            f" (parsed by Python {RUNNING}); the project targets Python 3.99, "
            "so run smelt on it, e.g. `uvx -p 3.99 smelt check`"
        )

    def test_reads_python_version_file(self, tmp_path: Path) -> None:
        (tmp_path / ".python-version").write_text("3.99.1\n", encoding="utf-8")

        assert "`uvx -p 3.99 smelt check`" in python_note(tmp_path)

    def test_older_target_adds_nothing(self, tmp_path: Path) -> None:
        (tmp_path / ".python-version").write_text("3.8\n", encoding="utf-8")

        assert python_note(tmp_path) == f" (parsed by Python {RUNNING})"


class TestSyntaxError:
    def test_error_carries_the_python_note(self, tmp_path: Path) -> None:
        root = _project(tmp_path, "def broken(:\n", requires=">=3.99")

        with pytest.raises(AnalysisError, match=r"uvx -p 3\.99 smelt check"):
            violations(root)

    @pytest.mark.skipif(sys.version_info >= (3, 14), reason="3.14 parses it")
    def test_newer_syntax_on_an_older_python(self, tmp_path: Path) -> None:
        root = _project(tmp_path, PY314, requires=">=3.14")

        with pytest.raises(AnalysisError, match=r"uvx -p 3\.14 smelt check"):
            violations(root)


class TestNewerSyntax:
    @pytest.mark.skipif(sys.version_info < (3, 13), reason="PEP 696 needs 3.13")
    def test_type_parameter_defaults(self, tmp_path: Path) -> None:
        root = _project(tmp_path, PY313, requires=">=3.13")

        found = violations(root, CheckOptions(select=("SMT101",)))

        assert codes_at(found) == [("SMT101", "app/domain/service.py", 1)]

    @pytest.mark.skipif(sys.version_info < (3, 14), reason="PEP 750/758 need 3.14")
    def test_template_strings_and_bare_except_tuples(self, tmp_path: Path) -> None:
        root = _project(tmp_path, PY314, requires=">=3.14")

        found = violations(root, CheckOptions(select=("SMT101",)))

        assert codes_at(found) == [("SMT101", "app/domain/service.py", 1)]
