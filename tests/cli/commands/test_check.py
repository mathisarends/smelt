"""The native launcher's `smelt check` writes the report of the Python CLI."""

from __future__ import annotations

import os
import subprocess
import sysconfig
from pathlib import Path

import pytest

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
# The Python CLI reports the syntax error the rules of these variants reach.
PYTHON_ONLY = {
    ("rust_parity_syntax_error", "concise"),
    ("rust_parity_syntax_error", "json"),
}
# Workspace members a check starts in, with and without their own config.
MEMBERS = (
    "rust_parity_member_configs/libs/core",
    "rust_parity_member_configs/libs/tools/src",
    "rust_parity_workspace/services/api",
)
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
