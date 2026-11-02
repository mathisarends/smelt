from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

OPTIONS = CheckOptions(select=("SMT309", "SMT310", "SMT311", "SMT325"))


def _project(
    tmp_path: Path, source: str, *, policy: str, version: str | None = None
) -> None:
    minimum = f'\n  python_version: "{version}"' if version else ""
    write_project(
        tmp_path,
        {
            "smelt.yml": f"version: 1\nproject:\n  root_packages: [app]{minimum}\nstructure:\n  contracts:\n    source:\n      match: app\n      {policy}\n",
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


@pytest.mark.parametrize("source", ['"""Introduction."""', "VALUE = 1"])
def test_docstring_allow_policy(tmp_path: Path, source: str) -> None:
    _project(tmp_path, source, policy="module_docstrings: allow")

    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(
    "source",
    [
        "",
        "# Module introduction.\n",
        '""""""',
        '"""  \n\t """',
        'b"Introduction"',
        'f"Introduction"',
        'VALUE = 1\n"""Too late."""',
        'class Service:\n    """Class contract."""',
        'def calculate():\n    """Function contract."""',
    ],
)
def test_required_docstring_reports_missing_or_blank_introduction(
    tmp_path: Path, source: str
) -> None:
    _project(tmp_path, source, policy="module_docstrings: require")
    before = (tmp_path / "app/module.py").read_bytes()

    [finding] = violations(tmp_path)

    assert finding.code == "SMT325"
    assert finding.rule == "missing-module-docstring"
    assert finding.severity.value == "error"
    assert (finding.path, finding.line, finding.column) == ("app/module.py", 1, 1)
    assert finding.source_module == "app.module"
    assert finding.expected == {"contract": "source", "module_docstrings": "require"}
    assert (tmp_path / "app/module.py").read_bytes() == before


@pytest.mark.parametrize(
    "source",
    [
        '"""Module introduction."""',
        "# comment\n'Introduction'",
        '#!/usr/bin/env python\n# coding: utf-8\n"""Einführung."""\nfrom __future__ import annotations',
        'r"""Multiline\nintroduction."""\nVALUE = 1',
    ],
)
def test_required_docstring_accepts_initial_text(tmp_path: Path, source: str) -> None:
    _project(tmp_path, source, policy="module_docstrings: require")

    assert violations(tmp_path, OPTIONS) == []


@pytest.mark.parametrize(
    "path", ["app/__init__.py", "app/__main__.py", "app/nested/__init__.py"]
)
def test_required_docstring_includes_empty_initializers_and_entry_points(
    tmp_path: Path, path: str
) -> None:
    _project(tmp_path, '"""Introduction."""', policy="module_docstrings: require")
    write_project(tmp_path, {path: ""})

    assert [(v.path, v.code) for v in violations(tmp_path)] == [(path, "SMT325")]


@pytest.mark.parametrize(
    ("mode", "source", "code"),
    [("require", "VALUE = 1", "SMT325"), ("forbid", '"""Introduction."""', "SMT309")],
)
def test_docstring_file_exceptions_require_a_reason(
    tmp_path: Path, mode: str, source: str, code: str
) -> None:
    _project(
        tmp_path,
        f"# smelt: ignore-file[{code}] -- Compatibility facade documented elsewhere.\n{source}",
        policy=f"module_docstrings: {mode}",
    )

    outcome = check(tmp_path)

    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1

    write_project(
        tmp_path,
        {"app/module.py": f"# smelt: ignore-file[{code}]\n{source}"},
    )

    assert [v.code for v in violations(tmp_path)] == ["SMT902"]


@pytest.mark.parametrize("outer", ["require", "forbid"])
def test_nested_docstring_convention_overrides_outer_in_both_directions(
    tmp_path: Path, outer: str
) -> None:
    inner = "forbid" if outer == "require" else "require"
    _project(
        tmp_path,
        "VALUE = 1" if outer == "require" else '"""Introduction."""',
        policy=f"module_docstrings: {outer}",
    )
    config = tmp_path / "smelt.yml"
    config.write_text(
        config.read_text()
        + f"    nested:\n      match: app.nested\n      module_docstrings: {inner}\n",
        encoding="utf-8",
    )
    write_project(
        tmp_path,
        {"app/nested/__init__.py": '"""Introduction."""' if inner == "forbid" else ""},
    )

    assert {v.path: v.code for v in violations(tmp_path, OPTIONS)} == {
        "app/module.py": "SMT325" if outer == "require" else "SMT309",
        "app/nested/__init__.py": "SMT309" if inner == "forbid" else "SMT325",
    }


def test_nested_allow_exempts_required_docstrings(tmp_path: Path) -> None:
    _project(tmp_path, '"""Introduction."""', policy="module_docstrings: require")
    config = tmp_path / "smelt.yml"
    config.write_text(
        config.read_text()
        + "    nested:\n      match: app.nested\n      module_docstrings: allow\n",
        encoding="utf-8",
    )
    write_project(tmp_path, {"app/nested/__init__.py": ""})

    assert violations(tmp_path, OPTIONS) == []


def test_required_docstrings_respect_source_inventory_and_exclusions(
    tmp_path: Path,
) -> None:
    _project(tmp_path, '"""Introduction."""', policy="module_docstrings: require")
    config = tmp_path / "smelt.yml"
    config.write_text(
        config.read_text().replace(
            "root_packages: [app]",
            "root_packages: [app]\n  exclude:\n    - path: app/generated.py\n      reason: Generated source.",
        ),
        encoding="utf-8",
    )
    write_project(
        tmp_path,
        {"app/generated.py": "VALUE = 1", "tests/test_module.py": "VALUE = 1"},
    )

    assert violations(tmp_path, OPTIONS) == []


def test_conflicting_docstring_conventions_are_configuration_errors(
    tmp_path: Path,
) -> None:
    _project(tmp_path, "VALUE = 1", policy="module_docstrings: require")
    config = tmp_path / "smelt.yml"
    config.write_text(
        config.read_text()
        + "    other:\n      match: app\n      module_docstrings: forbid\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="conflicting module docstring policies"):
        check(tmp_path, OPTIONS)


@pytest.mark.parametrize(
    "setting", ["rules: {missing-module-docstring: off}\n", "rules: {SMT325: off}\n"]
)
def test_required_docstring_respects_explicit_off(tmp_path: Path, setting: str) -> None:
    _project(tmp_path, "VALUE = 1", policy="module_docstrings: require")
    config = tmp_path / "smelt.yml"
    config.write_text(config.read_text() + setting, encoding="utf-8")

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
    config = tmp_path / "smelt.yml"
    config.write_text(
        config.read_text()
        + "    documented:\n      match: app.documented\n      module_docstrings: allow\n",
        encoding="utf-8",
    )
    write_project(tmp_path, {"app/documented/__init__.py": '"""Allowed here."""'})

    assert [v.path for v in violations(tmp_path, OPTIONS)] == ["app/module.py"]


@pytest.mark.parametrize(
    "source", ["", '"""Introduction."""\nfrom __future__ import annotations']
)
def test_absent_policy_preserves_existing_behavior(tmp_path: Path, source: str) -> None:
    _project(
        tmp_path,
        source,
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
