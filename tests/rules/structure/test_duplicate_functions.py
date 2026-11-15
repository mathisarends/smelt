from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT352",))
HELPER = (
    "def {name}(exact: bool) -> str:\n"
    "{doc}"
    "    if exact:\n"
    "        return 'Text entered.'\n"
    "    # Never echo text that surrounds a secret.\n"
    "    return 'Text entered with a slip.'\n"
)


def _helper(name: str = "_secret_entered", doc: str = "") -> str:
    return HELPER.replace("{name}", name).replace("{doc}", doc)


def _project(tmp_path: Path, files: dict[str, str], *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/tools/__init__.py": "",
            **files,
        },
    )


def _sites(root: Path) -> list[tuple[str | None, int | None, str]]:
    return [
        (item.path, item.line, item.expected["function"])
        for item in violations(root, SELECT)
        if item.expected is not None
    ]


def test_reports_each_copy_from_the_issue(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            "app/tools/keyboard.py": _helper(),
            "app/tools/typing.py": "VALUE = 1\n\n\n" + _helper(),
        },
    )

    first, second = violations(root, SELECT)

    assert (first.code, first.path, first.line, first.column) == (
        "SMT352",
        "app/tools/keyboard.py",
        1,
        1,
    )
    assert first.expected is not None
    assert second.expected is not None
    fingerprint = first.expected["fingerprint"]
    assert second.expected["fingerprint"] == fingerprint
    assert first.expected == {
        "function": "_secret_entered",
        "fingerprint": fingerprint,
        "count": 2,
        "copies": [
            {"path": "app/tools/typing.py", "line": 4, "function": "_secret_entered"}
        ],
        "copies_truncated": False,
        "common_package": "app.tools",
        "automatic_rewrite": False,
    }
    assert (second.path, second.line) == ("app/tools/typing.py", 4)


def test_ignores_names_docstrings_comments_and_formatting(tmp_path: Path) -> None:
    reformatted = (
        "def entered(exact: bool) -> str:\n"
        "    if exact:\n"
        '        return "Text entered."\n'
        '    return "Text entered with a slip."\n'
    )
    root = _project(
        tmp_path,
        {
            "app/tools/keyboard.py": _helper(doc='    """Describe it."""\n'),
            "app/tools/typing.py": reformatted,
            "app/tools/mouse.py": _helper("other_name"),
        },
    )

    assert [site[2] for site in _sites(root)] == [
        "_secret_entered",
        "other_name",
        "entered",
    ]


def test_reports_copies_within_one_module(tmp_path: Path) -> None:
    source = _helper("first") + "\n\n" + _helper("second")
    root = _project(tmp_path, {"app/tools/keyboard.py": source})

    assert _sites(root) == [
        ("app/tools/keyboard.py", 1, "first"),
        ("app/tools/keyboard.py", 8, "second"),
    ]


def test_accepts_functions_that_differ(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            "app/tools/keyboard.py": _helper(),
            "app/tools/typing.py": _helper().replace("slip", "typo"),
            "app/tools/mouse.py": _helper().replace("exact: bool", "exact: int"),
            "app/tools/scroll.py": "async " + _helper(),
        },
    )

    assert _sites(root) == []


def test_ignores_small_bodies_methods_nested_functions_and_tests(
    tmp_path: Path,
) -> None:
    small = "def name(node: object) -> str:\n    return str(node)\n"
    method = "class Keys:\n" + "".join(
        f"    {line}\n" for line in _helper().splitlines()
    )
    nested = "def outer() -> None:\n" + "".join(
        f"    {line}\n" for line in _helper().splitlines()
    )
    root = _project(
        tmp_path,
        {
            "app/tools/keyboard.py": small + "\n\n" + method,
            "app/tools/typing.py": small + "\n\n" + nested,
            "app/tools/mouse.py": _helper(),
            "tests/test_keys.py": _helper(),
        },
    )

    assert _sites(root) == []


def test_compares_within_each_root_package(tmp_path: Path) -> None:
    config = "version: 1\nproject: {root_packages: [app, other]}\n"
    root = _project(
        tmp_path,
        {
            "app/tools/keyboard.py": _helper(),
            "other/__init__.py": "",
            "other/typing.py": _helper(),
        },
        config=config,
    )

    assert _sites(root) == []


def test_truncates_long_copy_lists(tmp_path: Path) -> None:
    files = {f"app/tools/copy{index}.py": _helper() for index in range(7)}

    found = violations(_project(tmp_path, files), SELECT)

    assert len(found) == 7
    assert found[0].expected is not None
    assert (
        found[0].expected["count"],
        len(found[0].expected["copies"]),
        found[0].expected["copies_truncated"],
    ) == (7, 5, True)


def test_rule_is_opt_in_and_uses_configured_severity(tmp_path: Path) -> None:
    files = {"app/tools/keyboard.py": _helper(), "app/tools/typing.py": _helper()}
    assert violations(_project(tmp_path / "default", files)) == []
    config = CONFIG + "rules: {duplicate-function: warning}\n"
    found = violations(_project(tmp_path / "enabled", files, config=config))
    assert [(item.code, item.severity) for item in found] == [
        ("SMT352", Severity.WARNING),
        ("SMT352", Severity.WARNING),
    ]
