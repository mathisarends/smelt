import json
from pathlib import Path

import pytest

from smelt.diagnostics.debt import Debt, DebtEntry, fingerprint
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, violations, write_project

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT321",))


@pytest.mark.parametrize("use_cache", [False, True])
def test_optional_debt_does_not_hide_a_new_function_after_an_old_one_is_fixed(
    tmp_path: Path, use_cache: bool
) -> None:
    before = (
        "def upload(content_type: str | None = None):\n    return content_type or 'default'\n"
        "def download(content_type: str | None = None):\n    return content_type or 'default'\n"
    )
    root = _project(tmp_path, before, CONFIG + "debt: debt.json\n")
    options = CheckOptions(select=("SMT321",), use_cache=use_cache)
    original = check(root, options)
    debt = Debt.from_violations(original.unfiltered, snippet_reader(original.context))
    debt.write(root / "debt.json")
    assert len({entry.fingerprint for entry in debt.entries}) == 2
    assert check(root, options).report.violations == []
    write_project(root, {"app/service.py": "\n\n" + before})
    assert check(root, options).report.violations == []
    write_project(
        root,
        {
            "app/service.py": "def upload(content_type: str | None = None):\n    return content_type or 'default'\n"
            "def download(content_type: str | None = None):\n    return content_type if content_type is not None else 'default'\n"
            "def new_operation(content_type: str | None = None):\n    return content_type or 'default'\n"
        },
    )
    changed = check(root, options)
    assert len(changed.report.violations) == 1
    assert changed.report.violations[0].expected is not None
    assert changed.report.violations[0].expected["scope"] == "new_operation"
    assert changed.report.in_debt == 1
    assert len(changed.resolved_debt) == 1
    assert changed.resolved_debt[0].scope == "download"


@pytest.mark.parametrize("version", [1, 2])
def test_unscoped_optional_debt_requires_review_instead_of_hiding_a_function(
    tmp_path: Path, version: int
) -> None:
    source = "def create(value: int | None = None):\n    return value or 7\n"
    root = _project(tmp_path, source, CONFIG + "debt: debt.json\n")
    [finding] = violations(root, OPTIONS)
    old_key = fingerprint(finding, "    return value or 7", legacy=True)
    path = root / "debt.json"
    if version == 1:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "violations": [
                        {
                            "fingerprint": old_key,
                            "code": finding.code,
                            "path": finding.path,
                            "message": finding.message,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    else:
        Debt([DebtEntry(old_key, finding.code, finding.path, finding.message)]).write(
            path
        )
    current = check(root, OPTIONS)
    assert [item.code for item in current.report.violations] == ["SMT321"]
    assert current.report.in_debt == 0
    assert len(current.resolved_debt) == 1


def test_optional_scopes_distinguish_same_named_methods_and_nested_functions(
    tmp_path: Path,
) -> None:
    source = (
        "class First:\n    def create(self, value: int | None = None):\n        return value or 7\n"
        "class Second:\n    async def create(self, value: int | None = None):\n        return value or 7\n"
        "def outer():\n    def create(value: int | None = None):\n        return value or 7\n"
    )
    root = _project(tmp_path, source)
    outcome = check(root, OPTIONS)
    assert {item.expected["scope"] for item in outcome.unfiltered if item.expected} == {
        "First.create",
        "Second.create",
        "outer.create",
    }
    debt = Debt.from_violations(outcome.unfiltered, snippet_reader(outcome.context))
    assert len({entry.fingerprint for entry in debt.entries}) == 3


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
    assert finding.expected == {
        "parameter": "options",
        "scope": "create",
        "fix_safety": "unsafe",
    }


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
        (
            3,
            {
                "parameter": "options",
                "scope": "FileSystem.__init__",
                "fix_safety": "unsafe",
            },
        ),
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
