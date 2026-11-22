import ast
import codecs
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from smelt.diagnostics.debt import Debt
from smelt.diagnostics.fixes import Applicability, FixAvailability
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


@dataclass(frozen=True, slots=True)
class _Example:
    """How to turn a rule's documented bad example into its good one."""

    config: str = ""
    path: str = "app/service.py"
    files: tuple[tuple[str, str], ...] = ()
    source: str | None = None
    expected: str | None = None


def _rules(entry: str, *, project: str = "") -> str:
    return (
        f"version: 2\nproject: {{root_packages: [app]{project}}}\nrules: {{{entry}}}\n"
    )


_PACKAGE_API = (
    ("app/observation/__init__.py", "from .models import Observe, Snapshot\n"),
    ("app/observation/models.py", "class Observe: ...\n\n\nclass Snapshot: ...\n"),
)

# Every rule whose findings carry a fix, with what its doc example needs.
_EXAMPLES = {
    "LAY004": _Example(ORDER_CONFIG),
    "TYP003": _Example(DEFAULT_CONFIG),
    "MET001": _Example(DEFAULT_CONFIG),
    "TYP004": _Example(
        _rules("typing/deprecated-async-contextmanager-return: warning")
    ),
    "TYP006": _Example(_rules("typing/prefer-deferred-annotations: hint")),
    "VIS008": _Example(
        _rules("visibility/reexport-convention: warning") + "style: {reexports: all}\n",
        path="app/events/__init__.py",
    ),
    "IMP003": _Example(
        _rules("imports/forbidden-import-style: error")
        + "style:\n  imports: {style: relative, max_relative_level: 1}\n",
        path="app/storage/service.py",
        files=(
            ("app/storage/__init__.py", ""),
            ("app/storage/models.py", "class Record: ...\n"),
            ("app/domain/__init__.py", "class Entity: ...\n"),
        ),
    ),
    "TYP008": _Example(
        _rules(
            "typing/prefer-inline-type-parameters: hint",
            project=', python_version: "3.12"',
        )
    ),
    "IDM001": _Example(_rules("idioms/bundled-instance-assignment: warning")),
    "LAY006": _Example(_rules("layout/callee-before-caller: hint")),
    "LAY011": _Example(_rules("layout/misplaced-module-logger: warning")),
    "MOD004": _Example(
        _rules("modeling/str-enum-value-convention: hint"),
        source="from enum import StrEnum, auto\n\n\nclass SignalingMessageType(StrEnum):\n    DESCRIPTION = auto()\n    READY = auto()\n",
        expected='from enum import StrEnum\n\n\nclass SignalingMessageType(StrEnum):\n    DESCRIPTION = "description"\n    READY = "ready"\n',
    ),
    "IMP004": _Example(
        _rules("imports/prefer-package-import: hint"),
        path="app/client.py",
        files=_PACKAGE_API,
        source="from app.observation import Observe\nfrom app.observation.models import Snapshot\n",
        expected="from app.observation import Observe, Snapshot\n",
    ),
    "MOD006": _Example(
        _rules("modeling/missing-dataclass-option: warning")
        + "style: {dataclass_options: [frozen, slots]}\n"
    ),
    "LAY008": _Example(_rules("layout/misplaced-private-constant: warning")),
    "CPX003": _Example(
        _rules("complexity/excess-positional-parameters: {severity: warning, max: 1}")
    ),
}


def _suggested(root: Path, outcome: CheckOutcome) -> int:
    """Apply the display fixes that --fix never writes, as a reader would."""
    count = 0
    for violation in outcome.report.violations:
        fix = violation.fix
        if fix is None or violation.path is None:
            continue
        assert fix.applicability is Applicability.DISPLAY
        path = root / violation.path
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for edit in sorted(fix.edits, key=lambda e: (e.line, e.column), reverse=True):
            start = sum(map(len, lines[: edit.line - 1])) + edit.column - 1
            end = sum(map(len, lines[: edit.end_line - 1])) + edit.end_column - 1
            text = "".join(lines)
            assert text[start:end] == edit.original
            lines = (text[:start] + edit.replacement + text[end:]).splitlines(
                keepends=True
            )
        path.write_text("".join(lines), encoding="utf-8")
        count += 1
    return count


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
    example = _EXAMPLES[rule.code]
    files = {
        "smelt.yml": example.config,
        "app/__init__.py": "",
        **dict(example.files),
        example.path: doc.bad if example.source is None else example.source,
    }
    root = write_project(tmp_path, files)
    options = CheckOptions(select=(rule.code,))

    fixed = _fix(root, options, unsafe=True)
    if not fixed.applied:
        fixed.applied = _suggested(root, check(root, options))
    rewritten = (root / example.path).read_text()

    assert fixed.applied >= 1
    expected = doc.good if example.expected is None else example.expected
    assert ast.dump(ast.parse(rewritten)) == ast.dump(ast.parse(expected))
    assert check(root, options).report.violations == []
    assert _fix(root, options, unsafe=True).files == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "    return options or 7  # smelt: ignore[TYP003, LAY006] -- legacy",
            "    return options or 7  # smelt: ignore[TYP003] -- legacy",
        ),
        (
            "    return options or 7  # smelt: ignore[complexity/constructor-logic] -- x",
            "    return options or 7  # smelt: ignore[complexity/excess-constructor-decisions] -- x",
        ),
        (
            "    return options or 7  # smelt: ignore[LAY006] -- legacy",
            "    return options or 7",
        ),
    ],
    ids=["unused-identifier", "retired-name", "whole-directive"],
)
def test_suppression_fixes_keep_what_is_still_needed(
    tmp_path: Path, line: str, expected: str
) -> None:
    root = _defaults(tmp_path, f"def create(options: int | None = None):\n{line}\n")
    options = CheckOptions(select=("TYP003", "LAY006", "MET001"))

    report = _fix(root, CheckOptions(select=("MET001",)), outcome=check(root, options))

    assert report.applied == 1
    assert (root / "app/service.py").read_text().splitlines()[1] == expected


def test_unknown_suppression_identifier_is_only_suggested(tmp_path: Path) -> None:
    source = "x = 1  # smelt: ignore[TYP999] -- legacy\n"
    root = _defaults(tmp_path, source)
    outcome = check(root, CheckOptions(select=("MET001",)))

    [violation] = outcome.report.violations
    assert violation.fix is not None
    assert violation.fix.applicability is Applicability.DISPLAY
    assert _fix(root, CheckOptions(select=("MET001",)), unsafe=True).applied == 0
    assert "-x = 1  # smelt: ignore[TYP999] -- legacy\n+x = 1\n" in fix_diff(
        outcome, suggestions=True
    )
    assert (root / "app/service.py").read_text() == source


def test_diff_shows_suggestions_only_when_asked(tmp_path: Path) -> None:
    source = "def connect(user_id: int, run_id: int) -> None:\n    ...\n"
    root = _project(tmp_path, source=source, config=_EXAMPLES["CPX003"].config)
    outcome = check(root, CheckOptions(select=("CPX003",)))

    assert fix_diff(outcome, unsafe=True) == ""
    assert "+def connect(user_id: int, *, run_id: int) -> None:\n" in fix_diff(
        outcome, suggestions=True
    )
    assert (root / "app/service.py").read_text() == source


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("CPX003", "def connect(user_id, run_id, *rest):\n    ...\n"),
        ("CPX003", "def connect(user_id, run_id, /):\n    ...\n"),
        (
            "LAY008",
            "class Client:\n    _LIMIT = 3\n\n    def run(self):\n        return self._LIMIT\n\n\nprint(Client()._LIMIT)\n",
        ),
        (
            "MOD006",
            "from dataclasses import dataclass\n\nOPTIONS = {}\n\n\n@dataclass(**OPTIONS)\nclass Point:\n    x: int\n",
        ),
        (
            "MOD004",
            "from enum import StrEnum, auto\n\n\nclass Kind(StrEnum):\n    @staticmethod\n    def _generate_next_value_(name, start, count, last_values):\n        return name\n\n    FIRST = auto()\n    SECOND = 'second'\n",
        ),
        (
            "IDM001",
            "class Point:\n    def __init__(self, x, y):\n        self.x, self.y = x(), y()\n",
        ),
        (
            "LAY006",
            "def parse():\n    return 1\n\nLIMIT = 3\n\ndef load(limit=LIMIT):\n    return parse()\n",
        ),
    ],
    ids=[
        "star-args",
        "positional-only",
        "outside-access",
        "unpacked-options",
        "custom-values",
        "call-values",
        "bound-default",
    ],
)
def test_findings_that_cannot_be_rewritten_carry_no_fix(
    tmp_path: Path, code: str, source: str
) -> None:
    root = _project(tmp_path, source=source, config=_EXAMPLES[code].config)

    found = check(root, CheckOptions(select=(code,))).report.violations

    assert found
    assert [violation.fix for violation in found] == [None] * len(found)
