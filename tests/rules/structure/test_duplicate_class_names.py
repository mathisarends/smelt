from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT355",))


def _project(tmp_path: Path, files: dict[str, str], *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/observation/__init__.py": "",
            **files,
        },
    )


def _sites(root: Path) -> list[tuple[str | None, int | None, str]]:
    return [
        (item.path, item.line, item.expected["class"])
        for item in violations(root, SELECT)
        if item.expected is not None
    ]


def test_reports_each_definition_from_the_issue(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            "app/observation/models.py": "class PageContent:\n    html: str\n",
            "app/observation/content.py": (
                "VALUE = 1\n\n\nclass PageContent:\n    markdown: str\n"
            ),
        },
    )

    first, second = violations(root, SELECT)

    assert (first.code, first.path, first.line, first.column) == (
        "SMT355",
        "app/observation/content.py",
        4,
        1,
    )
    assert first.message == (
        "Class PageContent is also defined in app.observation.models:1"
    )
    assert first.expected == {
        "class": "PageContent",
        "package": "app.observation",
        "others": [{"module": "app.observation.models", "line": 1}],
        "automatic_rewrite": False,
    }
    assert first.hint == (
        "Rename PageContent after its role in app.observation.content, "
        "or merge the classes if they describe the same thing."
    )
    assert (second.path, second.message) == (
        "app/observation/models.py",
        "Class PageContent is also defined in app.observation.content:4",
    )


def test_counts_the_package_initializer_as_part_of_the_package(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {
            "app/observation/__init__.py": "class TextState:\n    pass\n",
            "app/observation/state.py": "class TextState:\n    pass\n",
        },
    )

    assert _sites(root) == [
        ("app/observation/__init__.py", 1, "TextState"),
        ("app/observation/state.py", 1, "TextState"),
    ]


def test_ignores_reexports_private_nested_and_other_packages(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {
            "app/observation/__init__.py": (
                "from app.observation.models import PageContent\n"
            ),
            "app/observation/models.py": (
                "class PageContent:\n    pass\n\n\n"
                "class _Cache:\n    pass\n\n\n"
                "class Reader:\n    class Options:\n        pass\n"
            ),
            "app/observation/content.py": (
                "class _Cache:\n    pass\n\n\n"
                "class Writer:\n    class Options:\n        pass\n"
            ),
            "app/scripts/__init__.py": "",
            "app/scripts/models.py": "class PageContent:\n    pass\n",
        },
    )

    assert _sites(root) == []


def test_ignores_redefinition_within_one_module_and_tests(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            "app/observation/models.py": (
                "class Page:\n    pass\n\n\nclass Page:\n    pass\n"
            ),
            "tests/test_models.py": "class Page:\n    pass\n",
        },
    )

    assert _sites(root) == []


def test_lists_every_other_location(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        dict.fromkeys(
            ("app/observation/a.py", "app/observation/b.py", "app/observation/c.py"),
            "class TabStrip:\n    pass\n",
        ),
    )

    first = violations(root, SELECT)[0]

    assert first.expected is not None
    assert first.expected["others"] == [
        {"module": "app.observation.b", "line": 1},
        {"module": "app.observation.c", "line": 1},
    ]


def test_is_opt_in(tmp_path: Path) -> None:
    files = {
        "app/observation/models.py": "class Page:\n    pass\n",
        "app/observation/content.py": "class Page:\n    pass\n",
    }

    assert [
        item for item in violations(_project(tmp_path, files)) if item.code == "SMT355"
    ] == []
    enabled = _project(
        tmp_path / "enabled",
        files,
        config=CONFIG + "rules: {duplicate-class-name: warning}\n",
    )
    assert [item.code for item in violations(enabled)].count("SMT355") == 2
