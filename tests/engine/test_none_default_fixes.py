import codecs
import json
import subprocess
import sys
from pathlib import Path

import pytest

from smelt.diagnostics.debt import Debt
from smelt.engine.check import CheckOptions, snippet_reader
from smelt.engine.none_default_fixes import apply_none_default_fixes
from tests.helpers import check, write_project

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SOURCE = "def create(options: int | None = None):\n    return options or 7\n"
OPTIONS = CheckOptions(select=("SMT321",))


def _project(tmp_path: Path, source: str = SOURCE) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yaml": CONFIG, "app/__init__.py": "", "app/service.py": source},
    )


def test_checks_and_safe_fixes_preserve_truthiness_defaults(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outcome = check(root, OPTIONS)
    fixed = apply_none_default_fixes(outcome)
    assert len(outcome.report.violations) == 1
    assert fixed.files == []
    assert "--unsafe-fixes" in fixed.skipped[0]["reason"]
    assert (root / "app/service.py").read_text() == SOURCE


def test_unsafe_fix_preserves_falsy_values_and_fallback_laziness(
    tmp_path: Path,
) -> None:
    source = """\
calls = []
def fallback():
    calls.append(1)
    return 7
def create(options: object | None = None):
    return options or fallback()
"""
    root = _project(tmp_path, source)
    fixed = apply_none_default_fixes(check(root, OPTIONS), unsafe=True)
    assert fixed.files == ["app/service.py"]
    assert fixed.expressions == 1
    assert check(root, OPTIONS).report.violations == []
    assert apply_none_default_fixes(check(root, OPTIONS), unsafe=True).files == []
    script = root / "app/service.py"
    with script.open("a", encoding="utf-8") as handle:
        handle.write(
            "\nclass Falsy:\n    def __bool__(self):\n        raise AssertionError('truthiness must not run')\ncustom = Falsy()\nassert create(custom) is custom\n"
        )
        handle.write(
            "\nimport json\nprint(json.dumps([[create(value) for value in (0, '', [], False, None, 9)], calls]))\n"
        )
    result = subprocess.run(  # noqa: S603 - execute only the synthetic rewritten fixture
        [sys.executable, str(script)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == [[0, "", [], False, 7, 9], [1]]


def test_bom_crlf_unicode_comments_and_surrounding_source_are_preserved(
    tmp_path: Path,
) -> None:
    source = "# café\ndef create(ä: int | None = None):\n    label = 'ö'; return ä or 7  # default\n"
    root = _project(tmp_path, source)
    target = root / "app/service.py"
    target.write_bytes(codecs.BOM_UTF8 + source.replace("\n", "\r\n").encode())
    fixed = apply_none_default_fixes(check(root, OPTIONS), unsafe=True)
    assert fixed.files == ["app/service.py"]
    expected = source.replace("ä or 7", "(ä if ä is not None else (7))")
    assert (
        target.read_bytes() == codecs.BOM_UTF8 + expected.replace("\n", "\r\n").encode()
    )


@pytest.mark.parametrize(
    "expression",
    [
        "options or (\n        # intentional fallback\n        7\n    )",
        "options or \\\n        7",
    ],
)
def test_comments_and_continuations_inside_expression_require_manual_review(
    tmp_path: Path, expression: str
) -> None:
    source = f"def create(options: int | None = None):\n    return {expression}\n"
    root = _project(tmp_path, source)
    fixed = apply_none_default_fixes(check(root, OPTIONS), unsafe=True)
    assert fixed.files == []
    assert "manual editing" in fixed.skipped[0]["reason"]
    assert (root / "app/service.py").read_text() == source


def test_nested_defaults_and_expression_precedence(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "def create(options: int | None = None, other: int | None = None):\n    return [options or (other or 7), 2 * (options or (1 if ready else 3))]\n",
    )
    fixed = apply_none_default_fixes(check(root, OPTIONS), unsafe=True)
    assert fixed.expressions == 3
    assert fixed.skipped == []
    assert check(root, OPTIONS).report.violations == []


def test_source_changed_after_analysis_is_never_overwritten(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outcome = check(root, OPTIONS)
    edited = SOURCE + "# user edit\n"
    (root / "app/service.py").write_text(edited)
    fixed = apply_none_default_fixes(outcome, unsafe=True)
    assert fixed.files == []
    assert "changed" in fixed.skipped[0]["reason"]
    assert (root / "app/service.py").read_text() == edited


def test_scope_ignores_and_suppressions_bound_the_fix(tmp_path: Path) -> None:
    root = _project(tmp_path)
    suppressed = SOURCE.replace(
        "options or 7",
        "options or 7  # smelt: ignore[SMT321] -- intentional falsy fallback",
    )
    write_project(root, {"app/other.py": SOURCE, "app/legacy.py": suppressed})
    assert (
        apply_none_default_fixes(
            check(root, CheckOptions(select=("SMT321",), ignore=("SMT321",))),
            unsafe=True,
        ).files
        == []
    )
    fixed = apply_none_default_fixes(
        check(
            root,
            CheckOptions(select=("SMT321",), paths=("app/service.py", "app/legacy.py")),
        ),
        unsafe=True,
    )
    assert fixed.files == ["app/service.py"]
    assert (root / "app/other.py").read_text() == SOURCE
    assert (root / "app/legacy.py").read_text() == suppressed


def test_accepted_debt_is_only_fixed_when_explicitly_bypassed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    initial = check(root, OPTIONS)
    Debt.from_violations(
        initial.report.violations, snippet_reader(initial.context)
    ).write(root / "debt.json")
    write_project(root, {"smelt.yaml": CONFIG + "debt: debt.json\n"})
    assert apply_none_default_fixes(check(root, OPTIONS), unsafe=True).files == []
    assert (root / "app/service.py").read_text() == SOURCE
    fixed = apply_none_default_fixes(
        check(root, CheckOptions(select=("SMT321",), use_debt=False)), unsafe=True
    )
    assert fixed.files == ["app/service.py"]
