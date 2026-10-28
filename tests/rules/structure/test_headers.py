from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

OPTIONS = CheckOptions(select=("SMT309", "SMT310", "SMT311"))


def _project(
    tmp_path: Path, source: str, *, policy: str, version: str | None = None
) -> None:
    minimum = f'\n  python_version: "{version}"' if version else ""
    write_project(
        tmp_path,
        {
            "smelt.yaml": f"version: 1\nproject:\n  root_packages: [app]{minimum}\nstructure:\n  contracts:\n    source:\n      match: app\n      {policy}\n",
            "app/module.py": source,
        },
    )


@pytest.mark.parametrize(
    "docstring", ['"""Module introduction."""', '""""""', "# comment\n'Introduction'"]
)
def test_module_docstring_can_be_forbidden(tmp_path: Path, docstring: str) -> None:
    _project(tmp_path, docstring + "\nVALUE = 1\n", policy="module_docstrings: forbid")

    assert [v.code for v in violations(tmp_path, OPTIONS)] == ["SMT309"]


def test_class_function_and_ordinary_strings_are_preserved(tmp_path: Path) -> None:
    _project(
        tmp_path,
        'VALUE = "text"\nclass C:\n    """Class contract."""\ndef f():\n    """Function contract."""\n',
        policy="module_docstrings: forbid",
    )

    assert violations(tmp_path, OPTIONS) == []


def test_docstring_allow_policy(tmp_path: Path) -> None:
    _project(tmp_path, '"""Introduction."""', policy="module_docstrings: allow")

    assert violations(tmp_path, OPTIONS) == []


def test_future_can_be_forbidden_unconditionally(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from __future__ import annotations\ndef f(value: Later): pass\nclass Later: pass",
        policy="future_annotations: forbid",
        version="3.12",
    )

    assert [v.code for v in violations(tmp_path, OPTIONS)] == ["SMT310"]


@pytest.mark.parametrize("version", [None, "3.12", "3.13"])
def test_forward_references_before_314_are_retained(
    tmp_path: Path, version: str | None
) -> None:
    _project(
        tmp_path,
        "from __future__ import annotations\ndef f(value: Later): pass\nclass Later: pass",
        policy="future_annotations: when-needed",
        version=version,
    )

    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(
    "source", ["VALUE = 1", "def f(value: int) -> int: return value"]
)
def test_review_hint_for_plain_boilerplate(tmp_path: Path, source: str) -> None:
    _project(
        tmp_path,
        "from __future__ import annotations\n" + source,
        policy="future_annotations: when-needed",
        version="3.13",
    )

    found = violations(tmp_path, OPTIONS)

    assert [v.code for v in found] == ["SMT311"]
    assert found[0].severity.value == "hint"


@pytest.mark.parametrize(
    "source",
    [
        "def f(value: Later): pass\nclass Later: pass",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from external import Type\ndef f(value: Type): pass",
        "def f(value: 'Later'): pass",
        "def f(value: list[str] | None): pass",
    ],
)
def test_314_flags_future_even_with_typing_signals(tmp_path: Path, source: str) -> None:
    _project(
        tmp_path,
        "from __future__ import annotations\n" + source,
        policy="future_annotations: when-needed",
        version="3.14",
    )

    found = violations(tmp_path, OPTIONS)

    assert [v.code for v in found] == ["SMT310"]
    assert found[0].expected is not None
    assert found[0].expected["python"]["minimum"] == "3.14"
    assert found[0].expected["automatic_removal"] is False


def test_explicit_allow_exception_on_314(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from __future__ import annotations\nVALUE = 1",
        policy="future_annotations: allow",
        version="3.14",
    )

    assert violations(tmp_path, OPTIONS) == []


def test_other_future_features_are_unaffected(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from __future__ import generator_stop\n",
        policy="future_annotations: forbid",
    )

    assert violations(tmp_path, OPTIONS) == []


def test_nested_allow_overrides_outer_policy(tmp_path: Path) -> None:
    _project(tmp_path, '"""Forbidden here."""', policy="module_docstrings: forbid")
    config = tmp_path / "smelt.yaml"
    config.write_text(
        config.read_text()
        + "    documented:\n      match: app.documented\n      module_docstrings: allow\n",
        encoding="utf-8",
    )
    write_project(tmp_path, {"app/documented/__init__.py": '"""Allowed here."""'})

    assert [v.path for v in violations(tmp_path, OPTIONS)] == ["app/module.py"]


def test_absent_policy_preserves_existing_behavior(tmp_path: Path) -> None:
    _project(
        tmp_path,
        '"""Introduction."""\nfrom __future__ import annotations',
        policy="required: [module]",
    )

    assert violations(tmp_path, OPTIONS) == []


def test_requires_python_enables_version_check(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from __future__ import annotations\ndef f(x: Later): pass\nclass Later: pass",
        policy="future_annotations: when-needed",
    )
    write_project(
        tmp_path, {"pyproject.toml": '[project]\nrequires-python = ">=3.14"\n'}
    )

    found = violations(tmp_path, OPTIONS)

    assert [v.code for v in found] == ["SMT310"]
    assert found[0].expected is not None
    assert found[0].expected["python"]["source"] == "pyproject.toml"
