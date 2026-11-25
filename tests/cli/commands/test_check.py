"""The native launcher's `smelt check` writes the report of the Python CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest

from tests.helpers import write_project

NATIVE = Path(sysconfig.get_path("scripts")) / (
    "smelt.exe" if os.name == "nt" else "smelt"
)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PROJECTS = sorted(
    path.name
    for path in FIXTURES.glob("rust_parity*")
    if (path / "smelt.yml").is_file()
)
VARIANTS = {
    "text": ("--show-hints",),
    "concise": ("--format", "concise", "--select", "ALL"),
    "json": (
        "--format",
        "json",
        "--select",
        "ALL",
        "--strict-coverage",
        "--show-coverage",
    ),
    "github": ("--format", "github", "--fail-on", "warning", "--no-debt"),
}
# The Python CLI handles syntax errors and checks of installed re-exports.
PYTHON_ONLY = {
    ("rust_parity_syntax_error", "concise"),
    ("rust_parity_syntax_error", "json"),
    ("rust_parity_package_imports", "concise"),
    ("rust_parity_package_imports", "json"),
}
# Workspace members a check starts in, with and without their own config.
MEMBERS = (
    "rust_parity_member_configs/libs/core",
    "rust_parity_member_configs/libs/tools/src",
    "rust_parity_workspace/services/api",
)
# Checks that report only what changed since a commit.
COMPARISONS = {
    "changed": ("--changed", "--show-hints"),
    "json": ("--changed", "--format", "json", "--select", "ALL"),
    "base": ("--base", "base", "--format", "sarif"),
    "scoped": ("--changed", "shop", "--format", "concise"),
}
_FALLBACK = b"smelt trace: the Python CLI checks:"


def _check(
    root: Path, args: tuple[str, ...], *, native: bool
) -> tuple[int, bytes, bool]:
    env = {**os.environ, "SMELT_TRACE": "1"}
    env.pop("NO_COLOR", None)
    if native:
        env.pop("SMELT_NATIVE_CHECK", None)
    else:
        env["SMELT_NATIVE_CHECK"] = "0"
    done = subprocess.run(  # noqa: S603 - the installed launcher
        [str(NATIVE), "check", *args],
        cwd=root,
        capture_output=True,
        check=False,
        env=env,
        timeout=120,
    )
    return done.returncode, done.stdout, _FALLBACK in done.stderr


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("project", PROJECTS)
def test_native_check_writes_the_python_report(project: str, variant: str) -> None:
    if not NATIVE.is_file():
        pytest.skip("install the native client")
    root = FIXTURES / project
    code, report, fell_back = _check(root, VARIANTS[variant], native=True)

    assert (code, report) == _check(root, VARIANTS[variant], native=False)[:2]
    assert fell_back == ((project, variant) in PYTHON_ONLY)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("member", MEMBERS)
def test_native_check_in_a_member_writes_the_python_report(
    member: str, variant: str
) -> None:
    if not NATIVE.is_file():
        pytest.skip("install the native client")
    root = FIXTURES / member
    code, report, fell_back = _check(root, VARIANTS[variant], native=True)

    assert (code, report) == _check(root, VARIANTS[variant], native=False)[:2]
    assert not fell_back


@pytest.mark.parametrize(("pattern", "exit_code"), [(r"\p{L}+-\d+", 1), (r"(?=x)", 2)])
def test_native_check_agrees_with_python_on_skip_reason_patterns(
    tmp_path: Path, pattern: str, exit_code: int
) -> None:
    if not NATIVE.is_file():
        pytest.skip("install the native client")
    write_project(
        tmp_path,
        {
            "smelt.yml": (
                "version: 2\n"
                "project: {root_packages: [app], test_roots: [tests]}\n"
                "rules: {tests/missing-skip-reason: error}\n"
                f"tests: {{skip_reason_pattern: '{pattern}'}}\n"
            ),
            "app/__init__.py": "",
            "tests/test_app.py": "import pytest\n@pytest.mark.skip\ndef test_app(): ...\n",
        },
    )
    args = ("--format", "json")
    code, report, fell_back = _check(tmp_path, args, native=True)

    assert code == exit_code
    assert (code, report) == _check(tmp_path, args, native=False)[:2]
    assert fell_back == (exit_code == 2)


@pytest.mark.parametrize("variant", COMPARISONS)
def test_native_changed_check_writes_the_python_report(
    tmp_path: Path, variant: str
) -> None:
    if not NATIVE.is_file():
        pytest.skip("install the native client")
    root, _ = _changed_repository(tmp_path)
    code, report, fell_back = _check(root, COMPARISONS[variant], native=True)

    assert (code, report) == _check(root, COMPARISONS[variant], native=False)[:2]
    assert not fell_back


@pytest.mark.parametrize("variant", COMPARISONS)
def test_native_staged_check_writes_the_python_report(
    tmp_path: Path, variant: str
) -> None:
    if not NATIVE.is_file():
        pytest.skip("install the native client")
    root, restored = _changed_repository(tmp_path)
    staged = restored[: len(restored) // 2 + 1]
    for path in staged:
        _git(root, "add", path.relative_to(root).as_posix())
    # Edits only the working tree has, which a staged check must not read.
    with staged[0].open("a", encoding="utf-8") as source:
        source.write("\nimport os\nvalue = eval('1')\n")
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write("\nnot: [valid\n")
    args = ("--staged", *(arg for arg in COMPARISONS[variant] if arg != "--changed"))
    code, report, fell_back = _check(root, args, native=True)

    assert (code, report) == _check(root, args, native=False)[:2]
    assert not fell_back


def _changed_repository(tmp_path: Path) -> tuple[Path, list[Path]]:
    """A fixture project committed with every other source emptied, then restored."""
    root = tmp_path / "project"
    shutil.copytree(FIXTURES / "rust_parity_architecture", root)
    sources = sorted(root.rglob("*.py"))[::2]
    contents = {path: path.read_bytes() for path in sources}
    for path in sources:
        path.write_bytes(b"")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "branch", "base")
    # The restored files are what changed since the commit.
    for path, content in contents.items():
        path.write_bytes(content)
    return root, sources


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 -- Git receives a list of arguments without shell expansion.
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607 -- Use the Git executable selected by PATH.
        cwd=root,
        check=True,
        capture_output=True,
        timeout=30,
    )
