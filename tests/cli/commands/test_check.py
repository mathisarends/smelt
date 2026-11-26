"""The native launcher's `smelt check` writes the report of the Python CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest

from smelt.cli import main
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

_CLOCK_PLUGIN = """from smelt.rules import Rule, calls

clock = Rule(name="acme/domain-clock",
             match=calls.to("datetime.datetime.now").in_layer("domain"),
             message="Direct clock read", guide="Use acme.clock.now().")
rules = [clock]
"""


@pytest.fixture
def clock_project(tmp_path: Path) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": (
                "version: 2\nproject: {root_packages: [app]}\n"
                "extends: policy/base.yml\n"
                "architecture: {layers: {domain: {path: domain}, infra: {path: infra}}}\n"
                "rules: {acme/domain-clock: warning}\n"
            ),
            "policy/base.yml": "plugins: [rules.py]\n",
            "policy/rules.py": _CLOCK_PLUGIN,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/infra/__init__.py": "",
            "app/domain/service.py": (
                "from datetime import datetime as DT\n"
                "now = DT.now()\n"
                "known = DT.now()  # smelt: ignore[acme/domain-clock] -- explicit exception\n"
            ),
            "app/domain/other.py": "from datetime import datetime as DT\ndef other(DT): return DT.now()\n",
            "app/infra/service.py": "from datetime import datetime as DT\nnow = DT.now()\n",
        },
    )


def test_project_calls_report_the_alias_and_respect_layer_shadowing_and_ignore(
    clock_project: Path,
) -> None:
    args = ("--select", "acme/domain-clock", "--format", "json", "--fail-on", "warning")
    code, output, _ = _check(clock_project, args, native=True)
    assert (code, output) == _check(clock_project, args, native=False)[:2]
    assert code == 1, output
    report = json.loads(output)
    assert report["scope"]["rules"] == ["acme/domain-clock"]
    assert [
        (v["code"], v["path"], v["line"], v["severity"]) for v in report["violations"]
    ] == [("acme/domain-clock", "app/domain/service.py", 2, "warning")]


def test_staged_project_rule_and_source_come_from_the_index(
    clock_project: Path,
) -> None:
    source = clock_project / "app/domain/service.py"
    contents = source.read_bytes()
    source.write_bytes(b"")
    _git(clock_project, "init", "-q")
    _git(clock_project, "add", "-A")
    _git(clock_project, "commit", "-qm", "base")
    source.write_bytes(contents)
    _git(clock_project, "add", "app/domain/service.py")
    (clock_project / "policy/rules.py").write_text("rules = []\n", encoding="utf-8")
    source.write_bytes(b"")
    code, output, _ = _check(
        clock_project,
        ("--staged", "--format", "json", "--fail-on", "warning"),
        native=True,
    )
    assert code == 1, output
    assert [v["code"] for v in json.loads(output)["violations"]] == [
        "acme/domain-clock"
    ]


def test_exported_rules_need_individual_activation(clock_project: Path) -> None:
    plugin = clock_project / "policy/rules.py"
    second = (
        'other = Rule(name="team/clock", match=calls.to("datetime.datetime.now").in_layer("domain"), '
        'message="Direct read", guide="Use the clock.")\n'
        "rules = [clock, other]"
    )
    plugin.write_text(
        _CLOCK_PLUGIN.replace("rules = [clock]", second), encoding="utf-8"
    )
    _, output, _ = _check(clock_project, ("--format", "json"), native=True)
    assert "team/clock" not in json.loads(output)["scope"]["rules"]
    _, selected, _ = _check(
        clock_project, ("--select", "team/clock", "--format", "json"), native=True
    )
    report = json.loads(selected)
    assert report["scope"]["rules"] == ["team/clock"]
    assert [entry["line"] for entry in report["violations"]] == [2, 3]


def test_project_rule_discovery_and_scoped_guide(
    clock_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(clock_project / "smelt.yml")
    assert (
        main(["--config", config, "explain", "acme/domain-clock", "--format", "json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["canonical"] == "Use acme.clock.now()."
    assert main(["--config", config, "rules", "--format", "json"]) == 0
    assert "acme/domain-clock" in {
        r["name"] for r in json.loads(capsys.readouterr().out)
    }
    assert main(["--config", config, "context", "app/domain/future.py", "--guide"]) == 0
    assert "Use acme.clock.now()." in capsys.readouterr().out
    assert main(["--config", config, "context", "app/infra/future.py", "--guide"]) == 0
    assert "Use acme.clock.now()." not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("contents", "error"),
    [
        ("rules = [missing]", "previously declared rule"),
        (_CLOCK_PLUGIN.replace("[clock]", "[clock, clock]"), "duplicate rule"),
        ("raise RuntimeError('executed')", "expected a rule declaration"),
    ],
)
def test_invalid_project_rule_is_a_config_error(
    clock_project: Path,
    contents: str,
    error: str,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (clock_project / "policy/rules.py").write_text(contents, encoding="utf-8")
    assert main(["--config", str(clock_project / "smelt.yml"), "check"]) == 2
    assert error in capsys.readouterr().err


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


def test_native_check_writes_workspace_layout_findings(tmp_path: Path) -> None:
    if not NATIVE.is_file():
        pytest.skip("install the native client")
    write_project(
        tmp_path,
        {
            "smelt.yml": (
                "version: 2\n"
                "project: {workspace: uv, workspace_layout: src}\n"
                "rules:\n"
                "  architecture/undeclared-member-dependency: error\n"
                "  layout/workspace-layout-convention: error\n"
            ),
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*"]\n',
            "services/backend/pyproject.toml": '[project]\nname = "backend"\n',
            "services/backend/backend/__init__.py": "",
        },
    )

    args = ("--format", "json")
    code, report, fell_back = _check(tmp_path, args, native=True)

    assert code == 1
    assert b"LAY013" in report
    assert (code, report) == _check(tmp_path, args, native=False)[:2]
    assert not fell_back


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
