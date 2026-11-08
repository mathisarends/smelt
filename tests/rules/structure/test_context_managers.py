from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT337",))
SOURCE = """\
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

@asynccontextmanager
async def exclusive() -> AsyncIterator[None]:
    yield
"""


def test_deprecated_annotation_reports_location_and_preserves_source(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, SOURCE)
    [finding] = violations(root, OPTIONS)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT337",
        "app/coordination.py",
        5,
        26,
    )
    assert finding.expected is not None
    assert finding.expected["suggested_annotation"] == "AsyncGenerator[None, None]"
    assert finding.expected["decorator"] == "contextlib.asynccontextmanager"
    assert (root / "app/coordination.py").read_text() == SOURCE


@pytest.mark.parametrize(
    ("imports", "decorator", "annotation"),
    [
        (
            "from contextlib import asynccontextmanager as acm\nfrom typing import AsyncIterator as AI",
            "acm",
            "AI[Resource]",
        ),
        (
            "import contextlib as cl\nimport collections.abc as abc",
            "cl.asynccontextmanager",
            "abc.AsyncIterator[Resource]",
        ),
        (
            "import contextlib\nimport collections.abc",
            "contextlib.asynccontextmanager",
            '"collections.abc.AsyncIterator[Resource]"',
        ),
        (
            "import contextlib\nimport typing as t",
            "contextlib.asynccontextmanager",
            '"t.AsyncIterator[Resource]"',
        ),
        (
            "from contextlib import asynccontextmanager\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from collections.abc import AsyncIterator",
            "asynccontextmanager",
            '"AsyncIterator[Resource]"',
        ),
        (
            "from contextlib import asynccontextmanager\nfrom collections.abc import AsyncIterator",
            "asynccontextmanager",
            "AsyncIterator[dict[str, tuple[int, str]]]",
        ),
    ],
)
def test_import_aliases_and_quoted_annotations(
    tmp_path: Path, imports: str, decorator: str, annotation: str
) -> None:
    root = _project(
        tmp_path,
        f"{imports}\n@{decorator}\nasync def resource() -> {annotation}:\n    yield Resource()\n",
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    yielded_type = "dict[str, tuple[int, str]]" if "dict[" in annotation else "Resource"
    assert (
        finding.expected["suggested_annotation"]
        == f"AsyncGenerator[{yielded_type}, None]"
    )


def test_methods_and_nested_functions_are_checked(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "from collections.abc import AsyncIterator\nfrom contextlib import asynccontextmanager\nclass Coordinator:\n    @asynccontextmanager\n    async def exclusive(self) -> AsyncIterator[None]:\n        yield\ndef factory():\n    @asynccontextmanager\n    async def resource() -> AsyncIterator[str]:\n        yield 'ready'\n    return resource\n",
    )
    assert [(v.code, v.line) for v in violations(root, OPTIONS)] == [
        ("SMT337", 5),
        ("SMT337", 9),
    ]


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace("@asynccontextmanager\n", ""),
        SOURCE.replace("AsyncIterator", "AsyncGenerator").replace(
            "[None]", "[None, None]"
        ),
        SOURCE.replace("AsyncIterator", "AsyncGenerator"),
        SOURCE.replace("from contextlib", "from custom_contextlib"),
        SOURCE.replace("from collections.abc", "from custom_types"),
        SOURCE.replace("from contextlib", "from .contextlib"),
        SOURCE.replace("async def", "def"),
        SOURCE.replace("AsyncIterator[None]", '"not valid ["'),
        SOURCE.replace("AsyncIterator[None]", "UnknownIterator[None]"),
        SOURCE.replace("AsyncIterator[None]", "list[AsyncIterator[None]]"),
        SOURCE.replace(" -> AsyncIterator[None]", ""),
        "def factory():\n    from collections.abc import AsyncIterator\n    from contextlib import asynccontextmanager\n    @asynccontextmanager\n    async def resource() -> AsyncIterator[None]:\n        yield\n    return resource\n",
    ],
)
def test_valid_and_unknown_neighbors_are_preserved(tmp_path: Path, source: str) -> None:
    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize(
    "shadow",
    [
        "asynccontextmanager = custom",
        "AsyncIterator = custom",
        "def helper(AsyncIterator): pass",
        "class AsyncIterator: pass",
        "from other import *",
        "def helper[AsyncIterator](): pass",
        "del asynccontextmanager",
    ],
)
def test_shadowed_bindings_are_not_guessed(tmp_path: Path, shadow: str) -> None:
    root = _project(tmp_path, SOURCE + shadow + "\n")
    assert violations(root, OPTIONS) == []


def test_module_attribute_rebinding_is_not_guessed(tmp_path: Path) -> None:
    source = "import contextlib as cl\nfrom collections.abc import AsyncIterator\ncl.asynccontextmanager = custom\n@cl.asynccontextmanager\nasync def resource() -> AsyncIterator[None]:\n    yield\n"
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_opt_in_package_override_and_suppression(tmp_path: Path) -> None:
    root = _project(tmp_path, SOURCE)
    assert violations(root) == []
    _project(
        tmp_path,
        SOURCE,
        CONFIG + "rules: {deprecated-async-contextmanager-return: warning}\n",
    )
    assert [v.code for v in violations(root)] == ["SMT337"]
    _project(
        tmp_path,
        SOURCE,
        CONFIG
        + "structure:\n  contracts:\n    legacy: {match: app, rules: {deprecated-async-contextmanager-return: off}}\n",
    )
    assert violations(root, OPTIONS) == []
    _project(
        tmp_path,
        SOURCE.replace(
            "AsyncIterator[None]:",
            "AsyncIterator[None]:  # smelt: ignore[SMT337] -- Legacy type checker contract.",
        ),
    )
    assert violations(root, OPTIONS) == []


def test_cached_results_follow_annotation_and_import_changes(tmp_path: Path) -> None:
    uncached = CheckOptions(select=("SMT337",), use_cache=False)
    for source, expected in (
        (SOURCE, ["SMT337"]),
        (SOURCE.replace("AsyncIterator", "AsyncGenerator"), []),
        (SOURCE.replace("from contextlib", "from custom_contextlib"), []),
        (SOURCE, ["SMT337"]),
    ):
        root = _project(tmp_path, source)
        cached = violations(root, OPTIONS)
        assert [v.code for v in cached] == expected
        assert cached == violations(root, uncached)


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/coordination.py": source},
    )
