from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT344",))


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/autofill.py": source},
    )


def test_reports_second_name_for_a_local_class(tmp_path: Path) -> None:
    source = "class PasswordData:\n    pass\n\n\nAutofillData = PasswordData\n"
    root = _project(tmp_path, source)
    [finding] = violations(root, SELECT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT344",
        "app/autofill.py",
        5,
        1,
    )
    assert finding.expected == {
        "alias": "AutofillData",
        "target": "PasswordData",
        "target_kind": "class",
        "form": "assignment",
        "automatic_rewrite": False,
    }
    assert finding.hint == "Use PasswordData directly and remove AutofillData."
    assert (root / "app/autofill.py").read_text() == source


@pytest.mark.parametrize(
    ("statement", "form"),
    [
        ("type AutofillData = PasswordData", "type"),
        ("AutofillData: TypeAlias = PasswordData", "TypeAlias"),
        ("AutofillData: typing.TypeAlias = PasswordData", "TypeAlias"),
    ],
)
def test_reports_type_alias_spellings(
    tmp_path: Path, statement: str, form: str
) -> None:
    source = (
        "import typing\nfrom typing import TypeAlias\n\n\n"
        f"class PasswordData:\n    pass\n\n\n{statement}\n"
    )
    [finding] = violations(_project(tmp_path, source), SELECT)
    assert finding.expected is not None
    assert finding.expected["form"] == form


@pytest.mark.parametrize(
    ("header", "statement", "target"),
    [
        ("from app.models import PasswordData", "Data = PasswordData", "PasswordData"),
        ("import pathlib", "Location = pathlib.Path", "pathlib.Path"),
        ("import xml.etree.ElementTree", "Tree = xml.etree.ElementTree.Element", "xml.etree.ElementTree.Element"),
    ],
)
def test_reports_imported_class_names(
    tmp_path: Path, header: str, statement: str, target: str
) -> None:
    [finding] = violations(_project(tmp_path, f"{header}\n\n{statement}\n"), SELECT)
    assert finding.expected is not None
    assert finding.expected["target"] == target


def test_reports_local_function_alias(tmp_path: Path) -> None:
    source = "def fetch_page():\n    return 1\n\n\nload_page = fetch_page\n"
    [finding] = violations(_project(tmp_path, source), SELECT)
    assert finding.message == "load_page is a second name for function fetch_page"


@pytest.mark.parametrize(
    "source",
    [
        "class A:\n    pass\n\n\nclass B:\n    pass\n\n\nEither = A | B\n",
        "class A:\n    pass\n\n\nMany = list[A]\n",
        "from typing import Annotated\n\n\nclass A:\n    pass\n\n\nNamed = Annotated[A, 'a']\n",
        "from typing import Any\n\nJson = Any\n",
        "import typing\n\nJson = typing.Any\n",
        "DEFAULT_LIMIT = 10\nLIMIT = DEFAULT_LIMIT\n",
        "from app.defaults import DEFAULT_LIMIT\n\nLIMIT = DEFAULT_LIMIT\n",
        "from app.loading import fetch\n\nload = fetch\n",
        "import sys\n\nif sys.version_info >= (3, 13):\n    from app.new import Reader\nelse:\n    from app.old import Reader\nTextReader = Reader\n",
        "import sys\n\n\nclass A:\n    pass\n\n\nif sys.version_info >= (3, 13):\n    B = A\n",
        "class A:\n    pass\n\n\nclass Holder:\n    Kind = A\n",
        "class A:\n    pass\n\n\nA = A\n",
        "type Box[T] = list[T]\n",
        "class A:\n    pass\n\n\ndef A():\n    return 1\n\n\nB = A\n",
    ],
)
def test_accepts_type_expressions_values_and_conditional_names(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), SELECT) == []


def test_ignores_tests(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_autofill.py": "class A:\n    pass\n\n\nB = A\n",
        },
    )
    assert violations(root, SELECT) == []


def test_reasoned_suppression_keeps_compatibility_name(tmp_path: Path) -> None:
    source = (
        "class PasswordData:\n    pass\n\n\n"
        "AutofillData = PasswordData  # smelt: ignore[SMT344] -- public API until 2.0\n"
    )
    assert violations(_project(tmp_path, source), SELECT) == []
