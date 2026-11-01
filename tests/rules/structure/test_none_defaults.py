from pathlib import Path

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT321",))


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/service.py": source,
        },
    )


@pytest.mark.parametrize(
    "annotation",
    [
        "Options | None",
        "None | Options",
        "int | str | None",
        '"Options | None"',
        "Optional[Options]",
        "Maybe[Options]",
        "typing.Optional[Options]",
        "Union[Options, None]",
        "Annotated[Options | None, 'metadata']",
    ],
)
def test_optional_annotations_report_the_defaulting_expression(
    tmp_path: Path, annotation: str
) -> None:
    root = _project(
        tmp_path,
        f"from typing import Optional, Optional as Maybe, Union, Annotated\nimport typing\ndef create(options: {annotation} = None):\n    return options or Options()\n",
    )

    found = violations(root, OPTIONS)

    assert len(found) == 1
    finding = found[0]
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT321",
        "app/service.py",
        4,
        12,
    )
    assert finding.expected == {"parameter": "options", "fix_safety": "unsafe"}


@pytest.mark.parametrize(
    "signature",
    [
        "options: Options | None = None, /",
        "*, options: Options | None = None",
        "other, options: Options | None = None",
        "self, options: Options | None = None",
    ],
)
def test_positional_keyword_and_method_parameters(
    tmp_path: Path, signature: str
) -> None:
    assert (
        len(
            violations(
                _project(
                    tmp_path,
                    f"async def create({signature}):\n    return options or Options()\n",
                ),
                OPTIONS,
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "source",
    [
        "def create(options=None):\n    return options or Options()",
        "def create(options: Options = None):\n    return options or Options()",
        "def create(options: list[int | None] = None):\n    return options or Options()",
        "def create(options: Options | None = DEFAULT):\n    return options or Options()",
        "def create(options: Options | None):\n    return options or Options()",
        "def create(options: Options | None = None):\n    return options if options is not None else Options()",
        "def create(options: Options | None = None):\n    if options or ready():\n        pass",
        "def create(options: Options | None = None):\n    while options or ready():\n        pass",
        "def create(options: Options | None = None):\n    assert options or ready()",
        "def create(options: Options | None = None):\n    return not (options or ready())",
        "def create(options: Options | None = None):\n    return 1 if options or ready() else 2",
        "def create(options: Options | None = None):\n    return [x for x in items if options or ready()]",
        "def create(options: Options | None = None):\n    return options or other or Options()",
        "def create(options: Options | None = None):\n    return self.options or Options()",
        "def create(options: Options | None = None):\n    def nested():\n        return options or Options()\n    return nested",
        "def create(options: Options | None = None):\n    return lambda: options or Options()",
    ],
)
def test_valid_neighbors_and_uncertain_cases_are_left_alone(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize(
    "binding",
    [
        "options = other",
        "options += other",
        "del options",
        "options := other",
        "for options in items:\n        pass",
        "import options",
        "import other as options",
        "def options(): pass",
        "class options: pass",
        "try:\n        work()\n    except Error as options:\n        pass",
        "match item:\n        case {'options': options}: pass",
    ],
)
def test_rebound_parameters_are_skipped(tmp_path: Path, binding: str) -> None:
    if ":=" in binding:
        binding = f"({binding})"
    root = _project(
        tmp_path,
        f"def create(options: Options | None = None):\n    {binding}\n    return options or Options()\n",
    )
    assert violations(root, OPTIONS) == []


def test_nested_function_uses_its_own_parameter_scope(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "def create(options: Options | None = None):\n    def nested(options):\n        options = other\n        return options or Options()\n    return options or Options()\n",
    )
    assert [v.line for v in violations(root, OPTIONS)] == [5]


def test_constructor_assignment_from_the_reported_example(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "class FileSystem:\n    def __init__(self, options: FileSystemOptions | None = None, *, download_directory: str | None = None):\n        self._options = options or FileSystemOptions()\n",
    )
    assert [(v.line, v.expected) for v in violations(root, OPTIONS)] == [
        (3, {"parameter": "options", "fix_safety": "unsafe"}),
    ]


def test_rule_is_opt_in_and_can_be_disabled_for_a_package(tmp_path: Path) -> None:
    source = (
        "def create(options: Options | None = None):\n    return options or Options()\n"
    )
    root = _project(tmp_path, source)
    assert violations(root) == []
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + "rules: {implicit-optional-default: warning}\nstructure:\n  contracts:\n    exception:\n      match: app\n      rules: {implicit-optional-default: off}\n"
        },
    )
    assert violations(root, OPTIONS) == []


def test_rule_name_selection_and_configuration_enable_the_review(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "def create(options: Options | None = None):\n    return options or Options()\n",
        CONFIG + "rules: {implicit-optional-default: warning}\n",
    )
    assert [v.code for v in violations(root)] == ["SMT321"]
    assert (
        len(violations(root, CheckOptions(select=("implicit-optional-default",)))) == 1
    )
