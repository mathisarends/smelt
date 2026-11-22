from __future__ import annotations

import ast
import gc
import sys
from pathlib import Path
from weakref import ref

import pytest

from smelt.analysis.parsing import AnalysisError, python_note, tree_nodes
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, codes_at, violations, write_project

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
            "smelt.yml": LAYERED_CONFIG,
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
            "so run smelt on it, e.g. `uvx -p 3.99 --from smelt-cli smelt check`"
        )

    def test_reads_python_version_file(self, tmp_path: Path) -> None:
        (tmp_path / ".python-version").write_text("3.99.1\n", encoding="utf-8")

        assert "`uvx -p 3.99 --from smelt-cli smelt check`" in python_note(tmp_path)

    def test_older_target_adds_nothing(self, tmp_path: Path) -> None:
        (tmp_path / ".python-version").write_text("3.8\n", encoding="utf-8")

        assert python_note(tmp_path) == f" (parsed by Python {RUNNING})"


class TestSyntaxError:
    def test_error_carries_the_python_note(self, tmp_path: Path) -> None:
        root = _project(tmp_path, "def broken(:\n", requires=">=3.99")

        with pytest.raises(
            AnalysisError, match=r"uvx -p 3\.99 --from smelt-cli smelt check"
        ):
            violations(root)

    @pytest.mark.skipif(sys.version_info >= (3, 14), reason="3.14 parses it")
    def test_newer_syntax_on_an_older_python(self, tmp_path: Path) -> None:
        root = _project(tmp_path, PY314, requires=">=3.14")

        with pytest.raises(
            AnalysisError, match=r"uvx -p 3\.14 --from smelt-cli smelt check"
        ):
            violations(root)


class TestNewerSyntax:
    def test_utf8_bom_keeps_imports_and_line_numbers(self, tmp_path: Path) -> None:
        root = _project(tmp_path, "")
        (root / "app/domain/service.py").write_text(
            "from app.infra import db\n", encoding="utf-8-sig"
        )

        found = violations(root, CheckOptions(select=("ARC001",)))

        assert codes_at(found) == [("ARC001", "app/domain/service.py", 1)]

    @pytest.mark.skipif(sys.version_info < (3, 13), reason="PEP 696 needs 3.13")
    def test_type_parameter_defaults(self, tmp_path: Path) -> None:
        root = _project(tmp_path, PY313, requires=">=3.13")

        found = violations(root, CheckOptions(select=("ARC001",)))

        assert codes_at(found) == [("ARC001", "app/domain/service.py", 1)]

    @pytest.mark.skipif(sys.version_info < (3, 14), reason="PEP 750/758 need 3.14")
    def test_template_strings_and_bare_except_tuples(self, tmp_path: Path) -> None:
        root = _project(tmp_path, PY314, requires=">=3.14")

        found = violations(root, CheckOptions(select=("ARC001",)))

        assert codes_at(found) == [("ARC001", "app/domain/service.py", 1)]


def test_tree_nodes_matches_ast_walk_for_a_module_and_its_parts() -> None:
    tree = ast.parse(
        "class Service:\n    def run(self, value):\n        return [value]\n"
    )
    function = tree.body[0].body[0]  # type: ignore[attr-defined]

    assert list(tree_nodes(tree)) == list(ast.walk(tree))
    assert list(tree_nodes(function)) == list(ast.walk(function))


def test_tree_nodes_walks_a_root_once() -> None:
    tree = ast.parse("value = 1\n")

    assert tree_nodes(tree) is tree_nodes(tree)


def test_tree_nodes_releases_an_unused_tree() -> None:
    tree = ast.parse("def run():\n    return 1\n")
    function = tree.body[0]
    tree_ref = ref(tree)
    function_ref = ref(function)
    tree_nodes(tree)
    tree_nodes(function)

    del tree, function
    gc.collect()

    assert tree_ref() is None
    assert function_ref() is None


def test_analyses_share_walks_instead_of_calling_ast_walk() -> None:
    """A direct walk repeats work that two dozen rules already did for the module."""
    package = Path(__file__).resolve().parents[2] / "smelt"
    rewriting = {"analysis/parsing.py", "analysis/ordering_rewrite.py"}
    direct = sorted(
        source.relative_to(package).as_posix()
        for source in package.rglob("*.py")
        if "ast.walk(" in source.read_text(encoding="utf-8")
    )

    assert set(direct) <= rewriting
