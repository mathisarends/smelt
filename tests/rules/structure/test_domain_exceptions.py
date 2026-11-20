from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

POLICY = """\
version: 2
project: {root_packages: [app]}
style:
  overrides:
    errors:
      match: app
      errors: {base: app.errors.AppError}
"""
SELECT = CheckOptions(select=("MOD008",))


def _project(
    tmp_path: Path,
    source: str,
    *,
    config: str = POLICY,
    extra: dict[str, str] | None = None,
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/errors.py": "class AppError(Exception):\n    pass\n",
            "app/files.py": source,
            **(extra or {}),
        },
    )


def _raised(root: Path) -> list[tuple[str | None, int | None, str]]:
    return [
        (item.path, item.line, item.expected["exception"])
        for item in violations(root, SELECT)
        if item.expected is not None
    ]


def test_reports_builtin_raised_for_a_domain_failure(tmp_path: Path) -> None:
    source = (
        "def read(path):\n"
        "    try:\n"
        "        return path.read_text()\n"
        "    except (OSError, ValueError):\n"
        "        raise\n"
        "    except Exception as error:  # surface unreadable files\n"
        '        raise ValueError(f"Could not read {path}") from error\n'
    )

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "MOD008",
        "app/files.py",
        7,
        9,
    )
    assert finding.message == (
        'Raise of builtin ValueError; contract "errors" expects a subclass of app.errors.AppError'
    )
    assert finding.expected == {
        "contract": "errors",
        "exception": "ValueError",
        "base": "app.errors.AppError",
        "automatic_rewrite": False,
    }
    assert finding.hint == (
        "Raise a subclass of AppError from app.errors that names the failure, "
        "or add ValueError to errors.allow when callers rely on it."
    )


def test_reports_bare_class_and_builtins_module_raises(tmp_path: Path) -> None:
    source = (
        "import builtins\n\n\n"
        "class Reader:\n"
        "    def open(self):\n"
        "        raise RuntimeError\n\n"
        "    async def close(self):\n"
        "        raise builtins.KeyError('closed')\n"
    )

    assert _raised(_project(tmp_path, source)) == [
        ("app/files.py", 6, "RuntimeError"),
        ("app/files.py", 9, "KeyError"),
    ]


def test_accepts_project_errors_reraises_and_allowed_builtins(
    tmp_path: Path,
) -> None:
    source = (
        "from app.errors import AppError\n\n\n"
        "def run(step):\n"
        "    try:\n"
        "        step()\n"
        "    except AppError as error:\n"
        "        raise error\n"
        "    except OSError:\n"
        "        raise\n"
        "    raise AppError('failed')\n\n\n"
        "def todo():\n"
        "    raise NotImplementedError\n\n\n"
        "def main():\n"
        "    raise SystemExit(1)\n"
    )

    assert _raised(_project(tmp_path, source)) == []


def test_allow_list_extends_the_defaults(tmp_path: Path) -> None:
    config = POLICY.replace(
        "{base: app.errors.AppError}",
        "{base: app.errors.AppError, allow: [TypeError]}",
    )
    source = (
        "def check(value):\n"
        "    if not isinstance(value, int):\n"
        "        raise TypeError(value)\n"
        "    raise ValueError(value)\n"
    )

    assert _raised(_project(tmp_path, source, config=config)) == [
        ("app/files.py", 4, "ValueError")
    ]


def test_ignores_names_that_shadow_builtins(tmp_path: Path) -> None:
    source = (
        "from app.errors import AppError as TimeoutError\n\n\n"
        "class ConnectionError(Exception):\n"
        "    pass\n\n\n"
        "def connect():\n"
        "    raise ConnectionError('refused')\n\n\n"
        "def wait():\n"
        "    raise TimeoutError('slow')\n"
    )

    assert _raised(_project(tmp_path, source)) == []


def test_exempts_protocol_dunders_and_pydantic_validators(tmp_path: Path) -> None:
    source = (
        "from pydantic import BaseModel, field_validator\n\n\n"
        "class Bag:\n"
        "    def __getattr__(self, name):\n"
        "        raise AttributeError(name)\n\n"
        "    def __getitem__(self, key):\n"
        "        raise KeyError(key)\n\n"
        "    def __init__(self, size):\n"
        "        raise ValueError(size)\n\n\n"
        "class Options(BaseModel):\n"
        "    size: int\n\n"
        "    @field_validator('size')\n"
        "    @classmethod\n"
        "    def _size(cls, value):\n"
        "        raise ValueError(value)\n"
    )

    assert _raised(_project(tmp_path, source)) == [("app/files.py", 12, "ValueError")]


def test_nested_package_can_lift_the_policy(tmp_path: Path) -> None:
    config = POLICY + "    cli:\n      match: app.cli\n      errors: {}\n"
    source = "def fail():\n    raise RuntimeError('boom')\n"
    root = _project(
        tmp_path,
        source,
        config=config,
        extra={"app/cli/__init__.py": "", "app/cli/main.py": source},
    )

    assert _raised(root) == [("app/files.py", 2, "RuntimeError")]


def test_ignores_tests(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "",
        extra={"tests/test_files.py": "def test_it():\n    raise ValueError\n"},
    )

    assert _raised(root) == []


def test_is_inactive_without_policy(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "def fail():\n    raise ValueError\n",
        config="version: 2\nproject: {root_packages: [app]}\n",
    )

    assert [item for item in violations(root) if item.code == "MOD008"] == []
