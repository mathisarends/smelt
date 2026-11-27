from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

_CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
_ENABLED = _CONFIG + "rules: {modeling/prefer-error-name: error}\n"
_SELECT = CheckOptions(select=("MOD013",))


def _project(tmp_path: Path, files: dict[str, str], config: str = _ENABLED) -> Path:
    write_project(tmp_path, {"smelt.yml": config, "app/__init__.py": "", **files})
    return tmp_path


@pytest.mark.parametrize(
    "base",
    [
        "Exception",
        "BaseException",
        "ValueError",
        "UserWarning",
        "KeyboardInterrupt",
        "ExceptionGroup[ValueError]",
        "errors.AppError",
    ],
)
def test_exception_classes_ending_in_exception_are_reported(
    tmp_path: Path, base: str
) -> None:
    source = f"from app import errors\nclass ParseException({base}): pass\n"
    root = _project(tmp_path, {"app/parsing.py": source})

    [finding] = violations(root)

    assert codes_at([finding]) == [("MOD013", "app/parsing.py", 2)]
    assert finding.expected == {
        "kind": "class",
        "name": "ParseException",
        "suggested_name": "ParseError",
        "automatic_rename": False,
    }
    assert (root / "app/parsing.py").read_text() == source


def test_subclasses_of_local_exceptions_are_reported_in_any_order(
    tmp_path: Path,
) -> None:
    source = (
        "class ReadException(_AppException): pass\n"
        "class _AppException(Exception): pass\n"
        "def load():\n    class LocalException(ReadException): pass\n"
    )
    root = _project(tmp_path, {"app/reading.py": source})

    assert codes_at(violations(root)) == [
        ("MOD013", "app/reading.py", 1),
        ("MOD013", "app/reading.py", 2),
        ("MOD013", "app/reading.py", 4),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "class ParseError(Exception): pass\n",
        "class Cancelled(Exception): pass\n",
        "class ExceptionHandler: pass\n",
        "class RecordedException(Record): pass\n",
        "class ParseException(Failure): pass\n",
        "class Exception(ValueError): pass\n",
        "from builtins import Exception as BuiltinException\n",
        "def handle_exception(error: Exception) -> None:\n    raise error\n",
    ],
)
def test_other_names_and_non_exception_classes_are_valid(
    tmp_path: Path, source: str
) -> None:
    root = _project(tmp_path, {"app/parsing.py": source})

    assert violations(root) == []


@pytest.mark.parametrize(
    ("path", "kind", "name", "suggested"),
    [
        ("app/exceptions.py", "module", "exceptions", "errors"),
        ("app/exception.py", "module", "exception", "errors"),
        ("app/_exceptions.py", "module", "_exceptions", "_errors"),
        ("app/exceptions/__init__.py", "package", "exceptions", "errors"),
    ],
)
def test_exception_modules_and_packages_are_reported(
    tmp_path: Path, path: str, kind: str, name: str, suggested: str
) -> None:
    root = _project(tmp_path, {path: ""})

    [finding] = violations(root)

    assert codes_at([finding]) == [("MOD013", path, None)]
    assert finding.expected == {
        "kind": kind,
        "name": name,
        "suggested_name": suggested,
        "automatic_rename": False,
    }


def test_occupied_errors_module_suggests_merging(tmp_path: Path) -> None:
    root = _project(tmp_path, {"app/exceptions.py": "", "app/errors.py": ""})

    [finding] = violations(root)

    assert finding.expected == {
        "kind": "module",
        "name": "exceptions",
        "suggested_name": None,
        "automatic_rename": False,
    }
    assert finding.hint == (
        'Merge the module into the existing "errors" module or package; '
        "update imports and facade exports."
    )


@pytest.mark.parametrize(
    "path",
    ["app/exception_handlers.py", "app/http_exceptions.py", "app/errors.py"],
)
def test_modules_mentioning_exceptions_are_valid(tmp_path: Path, path: str) -> None:
    root = _project(tmp_path, {path: ""})

    assert violations(root) == []


def test_tests_are_outside_scope(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            "app/parsing.py": "",
            "tests/test_parsing.py": "class FakeException(Exception): pass\n",
            "tests/exceptions.py": "",
        },
    )

    assert violations(root) == []


@pytest.mark.parametrize("selector", ["MOD013", "modeling/prefer-error-name"])
def test_default_is_off_and_explicit_selection_activates_the_rule(
    tmp_path: Path, selector: str
) -> None:
    root = _project(
        tmp_path,
        {"app/exceptions.py": "class AppException(Exception): pass\n"},
        _CONFIG,
    )

    assert violations(root) == []
    assert codes_at(violations(root, CheckOptions(select=(selector,)))) == [
        ("MOD013", "app/exceptions.py", None),
        ("MOD013", "app/exceptions.py", 1),
    ]


def test_package_override_and_suppression(tmp_path: Path) -> None:
    config = (
        _ENABLED
        + "style:\n  overrides:\n    legacy:\n      match: app.legacy\n"
        + "      rules: {modeling/prefer-error-name: off}\n"
    )
    root = _project(
        tmp_path,
        {
            "app/legacy/__init__.py": "class LegacyException(Exception): pass\n",
            "app/api.py": "class ApiException(Exception):  # smelt: ignore[MOD013] -- Published client name.\n    pass\n",
        },
        config,
    )

    assert violations(root, _SELECT) == []
