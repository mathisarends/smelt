from __future__ import annotations

import ast
import gc
from pathlib import Path
from weakref import ref

import pytest

from smelt.analysis.parsing import (
    AnalysisError,
    tree_nodes,
)
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, codes_at, violations, write_project

# Newer syntax the core parses on any Python; each file also breaks a layer boundary.
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


class TestSyntaxError:
    def test_error_names_the_file_and_line(self, tmp_path: Path) -> None:
        root = _project(tmp_path, "def broken(:\n", requires=">=3.99")

        with pytest.raises(
            AnalysisError, match=r"^app/domain/service\.py:1: syntax error"
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

    def test_type_parameter_defaults(self, tmp_path: Path) -> None:
        root = _project(tmp_path, PY313, requires=">=3.13")

        found = violations(root, CheckOptions(select=("ARC001",)))

        assert codes_at(found) == [("ARC001", "app/domain/service.py", 1)]

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


def test_tree_nodes_matches_ast_walk_for_every_field_shape() -> None:
    tree = ast.parse(
        "@decorator\n"
        "async def run[T](first: int, *rest, key=None) -> T:\n"
        "    global state\n"
        "    values = {name: [item async for item in source] for name in names}\n"
        "    match values:\n"
        "        case {'key': found, **others} if found:\n"
        "            return f'{found!r:>{width}}'\n"
        "    try:\n"
        "        del values[0]\n"
        "    except ValueError as error:\n"
        "        raise error from None\n"
        "    return {None: 1}, lambda x=1: x\n"
    )

    assert list(tree_nodes(tree)) == list(ast.walk(tree))


def test_tree_nodes_reads_a_constructed_node_with_omitted_fields() -> None:
    value = ast.Constant(1)
    present, omitted = ast.Return(value), ast.Return()

    assert tree_nodes(present) == (present, value)
    assert tree_nodes(omitted) == (omitted,)


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
