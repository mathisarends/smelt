import ast
import codecs
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from smelt.diagnostics.debt import Debt
from smelt.diagnostics.fixes import FixAvailability
from smelt.engine.check import CheckOptions, CheckOutcome, snippet_reader
from smelt.engine.fixes import FixReport, apply_fixes, fix_diff
from smelt.rules.base import RuleContract
from smelt.rules.registry import build_rule_set
from tests.helpers import check, write_project

ORDER_CONFIG = "\nversion: 2\nproject:\n  root_packages:\n  - app\narchitecture:\n  shared:\n  - app\nstyle:\n  overrides:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        layout/definition-order: error\n"
ORDER_SOURCE = "def _helper():\n    return 7\n\ndef api():\n    return _helper()\n"
ORDER = CheckOptions(select=("LAY004",))
DEFAULT_CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
DEFAULT_SOURCE = "def create(options: int | None = None):\n    return options or 7\n"
DEFAULTS = CheckOptions(select=("TYP003",))


def _project(
    tmp_path: Path, *, source: str = ORDER_SOURCE, config: str = ORDER_CONFIG
) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/service.py": source},
    )


def _defaults(tmp_path: Path, source: str = DEFAULT_SOURCE) -> Path:
    return _project(tmp_path, source=source, config=DEFAULT_CONFIG)


def _fix(
    root: Path,
    options: CheckOptions,
    *,
    outcome: CheckOutcome | None = None,
    unsafe: bool = False,
) -> FixReport:
    initial = outcome or check(root, options)
    _, report = apply_fixes(initial, lambda: check(root, options), unsafe=unsafe)
    return report


def _reasons(report: FixReport) -> list[str]:
    return [item["reason"] for item in report.skipped]


def test_normal_check_never_changes_source(tmp_path: Path) -> None:
    root = _project(tmp_path)
    source = root / "app/service.py"
    original = source.read_bytes()

    assert check(root, ORDER).report.failed
    assert source.read_bytes() == original


def test_refused_ordering_fix_keeps_source_and_reports_the_exact_trigger(
    tmp_path: Path,
) -> None:
    source = "def _helper(): pass\ndef api():\n    return globals()\n"
    root = _project(tmp_path, source=source)
    outcome = check(root, ORDER)

    report = _fix(root, ORDER, outcome=outcome)

    assert report.files == []
    finding = outcome.report.violations[0]
    assert finding.fix is None
    assert "app/service.py:3:12: globals" in str((finding.expected or {})["fix_reason"])
    assert (root / "app/service.py").read_text(encoding="utf-8") == source


def test_explicit_fix_and_second_run_are_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path)

    fixed = _fix(root, ORDER)
    after = check(root, ORDER)

    assert fixed.files == ["app/service.py"]
    assert fixed.applied == 1
    assert not after.report.failed
    assert _fix(root, ORDER).files == []
    assert (root / "app/service.py").read_text().lstrip().startswith("def api")


def test_builtin_union_fix_preserves_annotations_and_later_rebinding(
    tmp_path: Path,
) -> None:
    source = "from builtins import str as _Text\n\ndef _helper(value: _Text | bytes) -> _Text | bytes:\n    return value\n\ndef api(value):\n    return _helper(value)\n\n_Text = custom\n"
    root = _project(
        tmp_path,
        source=source,
        config=ORDER_CONFIG.replace(
            "project:\n", 'project:\n  python_version: "3.12"\n'
        ),
    )
    assert _fix(root, ORDER).files == ["app/service.py"]
    rewritten = (root / "app/service.py").read_text()
    assert (
        rewritten.index("def api")
        < rewritten.index("def _helper")
        < rewritten.index("_Text = custom")
    )
    assert "value: _Text | bytes) -> _Text | bytes" in rewritten
    assert not check(root, ORDER).report.violations


def test_nested_sections_are_fixed_in_one_run(tmp_path: Path) -> None:
    source = "class _Store:\n    def _load(self): pass\n    def get(self): return self._load()\n\nclass Api:\n    pass\n"
    root = _project(tmp_path, source=source)

    fixed = _fix(root, ORDER)

    rewritten = (root / "app/service.py").read_text()
    assert fixed.skipped == []
    assert rewritten.index("class Api") < rewritten.index("class _Store")
    assert rewritten.index("def get") < rewritten.index("def _load")
    assert not check(root, ORDER).report.violations


def test_source_bytes_keep_bom_crlf_and_permissions(tmp_path: Path) -> None:
    root = _project(tmp_path)
    source = root / "app/service.py"
    source.write_bytes(codecs.BOM_UTF8 + ORDER_SOURCE.replace("\n", "\r\n").encode())
    source.chmod(0o755)
    permissions = stat.S_IMODE(source.stat().st_mode)

    fixed = _fix(root, ORDER)
    data = source.read_bytes()

    assert fixed.files == ["app/service.py"]
    assert data.startswith(codecs.BOM_UTF8)
    assert data[len(codecs.BOM_UTF8) :].lstrip(b"\r\n").startswith(b"def api")
    assert data.count(codecs.BOM_UTF8) == 1
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert stat.S_IMODE(source.stat().st_mode) == permissions


def test_only_reported_path_scope_is_fixed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write_project(root, {"app/other.py": ORDER_SOURCE})

    fixed = _fix(root, CheckOptions(select=("LAY004",), paths=("app/service.py",)))

    assert fixed.files == ["app/service.py"]
    assert (root / "app/other.py").read_text() == ORDER_SOURCE


def test_disabled_rule_is_not_fixed(tmp_path: Path) -> None:
    root = _project(tmp_path)

    assert _fix(root, CheckOptions(select=("LAY003",))).files == []
    assert (root / "app/service.py").read_text() == ORDER_SOURCE


def test_suppressed_findings_are_not_fixed(tmp_path: Path) -> None:
    source = "# smelt: ignore-file[LAY004] -- legacy declaration order\n" + ORDER_SOURCE
    root = _project(tmp_path, source=source)

    assert _fix(root, ORDER).files == []
    assert (root / "app/service.py").read_text() == source


def test_accepted_debt_is_not_fixed_without_explicit_no_debt(tmp_path: Path) -> None:
    root = _project(tmp_path)
    initial = check(root, ORDER)
    Debt.from_violations(
        initial.report.violations, snippet_reader(initial.context)
    ).write(root / "debt.json")
    write_project(root, {"smelt.yml": ORDER_CONFIG + "\ndebt: debt.json\n"})

    assert _fix(root, ORDER).files == []
    assert _fix(root, CheckOptions(select=("LAY004",), use_debt=False)).files == [
        "app/service.py"
    ]


@pytest.mark.parametrize(
    ("source", "config", "reason"),
    [
        (
            ORDER_SOURCE,
            ORDER_CONFIG.replace("ordering: {}", "ordering: {fix: off}"),
            "disabled",
        ),
        (
            "if __name__ == '__main__':\n    main()\ndef api(): pass\n",
            ORDER_CONFIG,
            "main guard",
        ),
        ("# fmt: off\n" + ORDER_SOURCE, ORDER_CONFIG, "region"),
        (
            "class Service(Base):\n    def _helper(self): pass\n    def api(self): pass\n",
            ORDER_CONFIG,
            "class bases",
        ),
    ],
)
def test_manual_review_cases_carry_no_fix_and_name_the_reason(
    tmp_path: Path, source: str, config: str, reason: str
) -> None:
    root = _project(tmp_path, source=source, config=config)
    outcome = check(root, ORDER)

    assert _fix(root, ORDER, outcome=outcome).files == []
    assert all(v.fix is None for v in outcome.report.violations)
    assert any(
        reason in str((v.expected or {}).get("fix_reason"))
        for v in outcome.report.violations
    )
    assert (root / "app/service.py").read_text() == source


def test_user_edit_after_check_is_preserved(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outcome = check(root, ORDER)
    edited = ORDER_SOURCE + "# User edit after analysis.\n"
    (root / "app/service.py").write_text(edited)

    fixed = _fix(root, ORDER, outcome=outcome)

    assert fixed.files == []
    assert "changed" in _reasons(fixed)[0]
    assert (root / "app/service.py").read_text() == edited


def test_external_source_root_is_checked_but_never_rewritten(tmp_path: Path) -> None:
    config = ORDER_CONFIG.replace(
        "  root_packages:\n  - app\n",
        "  root_packages:\n  - app\n  source_roots: [../library/src]\n",
    )
    write_project(
        tmp_path,
        {"policy/smelt.yml": config, "library/src/app/service.py": ORDER_SOURCE},
    )

    fixed = _fix(tmp_path / "policy", ORDER)

    assert fixed.files == []
    assert "inside the project root" in _reasons(fixed)[0]
    assert (tmp_path / "library/src/app/service.py").read_text() == ORDER_SOURCE


@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation requires Windows privileges"
)
def test_source_replaced_by_symlink_after_check_is_preserved(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outcome = check(root, ORDER)
    target = root / "original.txt"
    target.write_text(ORDER_SOURCE)
    source = root / "app/service.py"
    source.unlink()
    source.symlink_to(target)

    fixed = _fix(root, ORDER, outcome=outcome)

    assert fixed.files == []
    assert "inside the project root" in _reasons(fixed)[0]
    assert source.is_symlink()
    assert target.read_text() == ORDER_SOURCE


def test_failed_replacement_leaves_original_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    outcome = check(root, ORDER)

    def fail(_path: Path, _target: Path) -> Path:
        raise PermissionError("target locked")

    monkeypatch.setattr(Path, "replace", fail)
    fixed = _fix(root, ORDER, outcome=outcome)

    assert fixed.files == []
    assert "locked" in _reasons(fixed)[0]
    assert (root / "app/service.py").read_text() == ORDER_SOURCE
    assert list((root / "app").glob(".smelt-fix-*.tmp")) == []


def test_unsafe_fixes_need_the_explicit_flag(tmp_path: Path) -> None:
    root = _defaults(tmp_path)
    outcome = check(root, DEFAULTS)

    fixed = _fix(root, DEFAULTS, outcome=outcome)

    assert len(outcome.report.violations) == 1
    assert fixed.files == []
    assert "--unsafe-fixes" in _reasons(fixed)[0]
    assert (root / "app/service.py").read_text() == DEFAULT_SOURCE


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
    root = _defaults(tmp_path, source)
    fixed = _fix(root, DEFAULTS, unsafe=True)
    assert fixed.files == ["app/service.py"]
    assert fixed.applied == 1
    assert check(root, DEFAULTS).report.violations == []
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
    root = _defaults(tmp_path, source)
    target = root / "app/service.py"
    target.write_bytes(codecs.BOM_UTF8 + source.replace("\n", "\r\n").encode())
    fixed = _fix(root, DEFAULTS, unsafe=True)
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
def test_comments_and_continuations_inside_expression_carry_no_fix(
    tmp_path: Path, expression: str
) -> None:
    source = f"def create(options: int | None = None):\n    return {expression}\n"
    root = _defaults(tmp_path, source)
    outcome = check(root, DEFAULTS)
    assert outcome.report.violations[0].fix is None
    assert _fix(root, DEFAULTS, outcome=outcome, unsafe=True).files == []
    assert (root / "app/service.py").read_text() == source


def test_nested_and_same_line_fixes_are_all_applied(tmp_path: Path) -> None:
    root = _defaults(
        tmp_path,
        "def create(options: int | None = None, other: int | None = None):\n    return [options or (other or 7), 2 * (options or (1 if ready else 3))]\n",
    )
    fixed = _fix(root, DEFAULTS, unsafe=True)
    assert fixed.applied == 3
    assert fixed.skipped == []
    assert check(root, DEFAULTS).report.violations == []


def test_scope_ignores_and_suppressions_bound_the_fix(tmp_path: Path) -> None:
    root = _defaults(tmp_path)
    suppressed = DEFAULT_SOURCE.replace(
        "options or 7",
        "options or 7  # smelt: ignore[TYP003] -- intentional falsy fallback",
    )
    write_project(root, {"app/other.py": DEFAULT_SOURCE, "app/legacy.py": suppressed})
    ignored = CheckOptions(select=("TYP003",), ignore=("TYP003",))
    assert _fix(root, ignored, unsafe=True).files == []
    scoped = CheckOptions(select=("TYP003",), paths=("app/service.py", "app/legacy.py"))
    assert _fix(root, scoped, unsafe=True).files == ["app/service.py"]
    assert (root / "app/other.py").read_text() == DEFAULT_SOURCE
    assert (root / "app/legacy.py").read_text() == suppressed


def test_overlapping_rules_are_applied_after_a_recheck(tmp_path: Path) -> None:
    source = "def _default():\n    return 7\ndef create(options: int | None = None):\n    return options or _default()\n"
    root = _project(
        tmp_path,
        source=source,
        config=ORDER_CONFIG.replace(
            "project:\n", 'project:\n  python_version: "3.12"\n'
        ),
    )
    options = CheckOptions(select=("LAY004", "TYP003"))

    fixed = _fix(root, options, unsafe=True)

    rewritten = (root / "app/service.py").read_text()
    assert fixed.applied == 2
    assert fixed.skipped == []
    assert rewritten.startswith("def create")
    assert "options if options is not None else" in rewritten


def test_diff_previews_permitted_fixes_without_writing(tmp_path: Path) -> None:
    root = _defaults(tmp_path)
    outcome = check(root, DEFAULTS)

    assert fix_diff(outcome) == ""
    diff = fix_diff(outcome, unsafe=True)

    assert diff.startswith("--- a/app/service.py\n+++ b/app/service.py\n")
    assert "-    return options or 7\n" in diff
    assert "+    return (options if options is not None else (7))\n" in diff
    assert (root / "app/service.py").read_text() == DEFAULT_SOURCE


# Every rule whose findings carry a fix, with a config that activates it.
_FIXABLE_CONFIGS = {"LAY004": ORDER_CONFIG, "TYP003": DEFAULT_CONFIG}


@pytest.mark.parametrize(
    "rule",
    [
        rule
        for rule in build_rule_set().rules
        if getattr(rule, "fix_availability", FixAvailability.NONE)
        is not FixAvailability.NONE
    ],
    ids=lambda rule: rule.code,
)
def test_fix_turns_the_bad_example_into_the_good_one(
    tmp_path: Path, rule: RuleContract
) -> None:
    doc = rule.explain()
    root = _project(tmp_path, source=doc.bad, config=_FIXABLE_CONFIGS[rule.code])
    options = CheckOptions(select=(rule.code,))

    fixed = _fix(root, options, unsafe=True)
    rewritten = (root / "app/service.py").read_text()

    assert fixed.applied >= 1
    assert ast.dump(ast.parse(rewritten)) == ast.dump(ast.parse(doc.good))
    assert check(root, options).report.violations == []
    assert _fix(root, options, unsafe=True).files == []
