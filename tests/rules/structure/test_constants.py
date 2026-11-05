from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app], test_roots: [tests]}\n"
OPTIONS = CheckOptions(select=("SMT334",))
DOWNLOADS = """\
class DownloadController:
    _DOWNLOADABLE_EXTENSIONS = {
        ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z", ".doc", ".docx",
        ".xls", ".xlsx", ".ppt", ".pptx", ".exe", ".dmg", ".apk", ".deb",
        ".rpm", ".iso", ".img",
    }

    def supports(self, extension: str) -> bool:
        return extension in self._DOWNLOADABLE_EXTENSIONS
"""


def test_download_extensions_belong_at_module_scope(tmp_path: Path) -> None:
    root = _project(tmp_path, DOWNLOADS)

    [finding] = violations(root, OPTIONS)

    assert finding.code == "SMT334"
    assert finding.path == "app/download.py"
    assert (finding.line, finding.column) == (2, 5)
    assert (finding.end_line, finding.end_column) == (2, 29)
    assert finding.source_module == "app.download"
    assert finding.expected == {
        "class": "DownloadController",
        "constant": "_DOWNLOADABLE_EXTENSIONS",
        "placement": "module",
        "visibility": "private",
        "automatic_rewrite": False,
    }
    assert finding.hint is not None
    assert "module scope" in finding.hint
    assert (root / "app/download.py").read_text(encoding="utf-8") == DOWNLOADS


def test_module_constant_is_valid_with_instance_and_local_fields(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        """
        _DOWNLOADABLE_EXTENSIONS = {".pdf", ".zip"}

        class DownloadController:
            def __init__(self):
                self._extensions = _DOWNLOADABLE_EXTENSIONS

            def supports(self, extension):
                _LOCAL_EXTENSIONS = {".pdf"}
                return extension in _DOWNLOADABLE_EXTENSIONS
        """,
    )

    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize(
    "declaration",
    [
        '_EXTENSIONS = {".pdf", ".zip"}',
        '_EXTENSIONS: set[str] = {".pdf"}',
        '_EXTENSIONS: "set[str]" = {".pdf"}',
        '_EXTENSIONS = [".pdf", ".zip"]',
        '_EXTENSIONS = (".pdf", ".zip")',
        '_EXTENSIONS = {"documents": [".pdf"], "archives": (".zip",)}',
        '_EXTENSIONS = {"weights": (-1, +2, -1.5)}',
        '_EXTENSIONS = ".pdf"',
        "_EXTENSIONS = 3",
        "_EXTENSIONS = None",
        "_EXTENSIONS = True",
        "_EXTENSIONS = {}",
        "_EXTENSIONS = []",
        "__EXTENSIONS = ()",
    ],
)
def test_private_literal_declarations_are_reported(
    tmp_path: Path, declaration: str
) -> None:
    [finding] = violations(
        _project(tmp_path, "class DownloadController:\n    " + declaration + "\n"),
        OPTIONS,
    )

    assert finding.expected is not None
    assert finding.expected["constant"] in {"_EXTENSIONS", "__EXTENSIONS"}


@pytest.mark.parametrize(
    "declaration",
    [
        'EXTENSIONS = {".pdf"}',
        '_extensions = {".pdf"}',
        '_Extensions = {".pdf"}',
        '__slots__ = ("_extensions",)',
        "_EXTENSIONS = load_extensions()",
        '_EXTENSIONS = frozenset({".pdf"})',
        "_EXTENSIONS = DEFAULT_EXTENSIONS",
        "_EXTENSIONS = {DEFAULT_EXTENSION}",
        '_EXTENSIONS = {".pdf": DEFAULT_HANDLER}',
        '_EXTENSIONS = {DEFAULT_EXTENSION: "pdf"}',
        '_EXTENSIONS = {**defaults, ".pdf": "pdf"}',
        '_EXTENSIONS = [*defaults, ".pdf"]',
        "_EXTENSIONS = {extension for extension in defaults}",
        "_EXTENSIONS: set[str]",
        '_EXTENSIONS += [".pdf"]',
        "_EXTENSIONS, _HANDLERS = values",
    ],
)
def test_other_fields_and_computed_values_remain_valid(
    tmp_path: Path, declaration: str
) -> None:
    root = _project(tmp_path, "class DownloadController:\n    " + declaration + "\n")

    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize(
    "header",
    [
        "class DownloadController(Controller):",
        "class DownloadController(object):",
        "class DownloadController(metaclass=Registry):",
        "@dataclass\nclass DownloadController:",
        "@model()\nclass DownloadController:",
        "class DownloadController(Enum):",
        "class DownloadController(StrEnum):",
        "class DownloadController(BaseModel):",
        "class DownloadController(Protocol):",
    ],
)
def test_class_contracts_are_excluded(tmp_path: Path, header: str) -> None:
    root = _project(tmp_path, header + '\n    _EXTENSIONS = {".pdf"}\n')

    assert violations(root, OPTIONS) == []


def test_guarded_chained_and_nested_class_declarations(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        """
        class DownloadController:
            if enabled:
                _EXTENSIONS = _ARCHIVES = {".zip"}

            class Options:
                _LIMIT = 3

            def configure(self):
                _LOCAL = {".pdf"}
                class LocalOptions:
                    _TIMEOUT = 10

            async def configure_async(self):
                _ASYNC_LOCAL = {".pdf"}
        """,
    )

    found = violations(root, OPTIONS)

    assert [(v.line, v.expected and v.expected["class"]) for v in found] == [
        (3, "DownloadController"),
        (3, "DownloadController"),
        (6, "Options"),
        (11, "LocalOptions"),
    ]


def test_rule_is_opt_in_and_error_severity_blocks(tmp_path: Path) -> None:
    root = _project(tmp_path, DOWNLOADS)
    assert violations(root) == []

    write_project(
        root, {"smelt.yml": CONFIG + "rules: {private-class-constant: error}\n"}
    )
    outcome = check(root)

    assert outcome.report.failed
    assert [v.code for v in outcome.report.violations] == ["SMT334"]


def test_closest_package_override_can_disable_the_rule(tmp_path: Path) -> None:
    root = _project(tmp_path, DOWNLOADS)
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + "rules: {private-class-constant: error}\n"
            + "structure:\n  contracts:\n    legacy:\n      match: app.legacy\n"
            + "      rules: {private-class-constant: off}\n",
            "app/legacy/__init__.py": "",
            "app/legacy/download.py": DOWNLOADS,
        },
    )

    assert [v.path for v in violations(root)] == ["app/download.py"]


def test_reasoned_suppression_and_test_classes_are_supported(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        'class DownloadController:\n    _EXTENSIONS = {".pdf"}  # smelt: ignore[SMT334] -- Subclasses override this contract.\n',
    )
    write_project(root, {"tests/test_download.py": DOWNLOADS})

    outcome = check(root, OPTIONS)

    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1


def test_moving_constant_invalidates_cached_findings(tmp_path: Path) -> None:
    root = _project(tmp_path, DOWNLOADS)
    assert [v.code for v in violations(root, OPTIONS)] == ["SMT334"]
    write_project(
        root,
        {
            "app/download.py": '_DOWNLOADABLE_EXTENSIONS = {".pdf", ".zip"}\n'
            + "class DownloadController:\n"
            + "    def supports(self, extension):\n"
            + "        return extension in _DOWNLOADABLE_EXTENSIONS\n"
        },
    )

    assert violations(root, OPTIONS) == []
    assert violations(root, CheckOptions(select=("SMT334",), use_cache=False)) == []


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/download.py": source},
    )
