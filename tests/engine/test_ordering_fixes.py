import codecs
import os
import stat
from pathlib import Path

import pytest

from smelt.diagnostics.debt import Debt
from smelt.engine.check import CheckOptions, snippet_reader
from smelt.engine.ordering_fixes import apply_ordering_fixes
from tests.helpers import check, write_project

CONFIG = """
version: 1
project: {root_packages: [app]}
architecture: {shared: [app]}
structure:
  contracts:
    order: {match: app, ordering: {}}
"""
SOURCE = "def _helper():\n    return 7\n\ndef api():\n    return _helper()\n"
OPTIONS = CheckOptions(select=("SMT313",))


def _project(tmp_path: Path, *, source: str = SOURCE, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yaml": config, "app/__init__.py": "", "app/service.py": source},
    )


def test_normal_check_never_changes_source(tmp_path: Path) -> None:
    root = _project(tmp_path)
    source = root / "app/service.py"
    original = source.read_bytes()

    assert check(root, OPTIONS).report.failed
    assert source.read_bytes() == original


def test_explicit_fix_and_second_run_are_idempotent(tmp_path: Path) -> None:
    root = _project(tmp_path)

    fixed = apply_ordering_fixes(check(root, OPTIONS))
    after = check(root, OPTIONS)

    assert fixed.files == ["app/service.py"]
    assert fixed.sections == 1
    assert not after.report.failed
    assert apply_ordering_fixes(after).files == []
    assert (root / "app/service.py").read_text().lstrip().startswith("def api")


def test_source_bytes_keep_bom_crlf_and_permissions(tmp_path: Path) -> None:
    root = _project(tmp_path)
    source = root / "app/service.py"
    source.write_bytes(codecs.BOM_UTF8 + SOURCE.replace("\n", "\r\n").encode())
    source.chmod(0o755)
    permissions = stat.S_IMODE(source.stat().st_mode)

    fixed = apply_ordering_fixes(check(root, OPTIONS))
    data = source.read_bytes()

    assert fixed.files == ["app/service.py"]
    assert data.startswith(codecs.BOM_UTF8)
    assert data[len(codecs.BOM_UTF8) :].lstrip(b"\r\n").startswith(b"def api")
    assert data.count(codecs.BOM_UTF8) == 1
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert stat.S_IMODE(source.stat().st_mode) == permissions


def test_only_reported_path_scope_is_fixed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    write_project(root, {"app/other.py": SOURCE})

    fixed = apply_ordering_fixes(
        check(root, CheckOptions(select=("SMT313",), paths=("app/service.py",)))
    )

    assert fixed.files == ["app/service.py"]
    assert (root / "app/other.py").read_text() == SOURCE


def test_disabled_rule_is_not_fixed(tmp_path: Path) -> None:
    root = _project(tmp_path)

    fixed = apply_ordering_fixes(check(root, CheckOptions(select=("SMT312",))))

    assert fixed.files == []
    assert (root / "app/service.py").read_text() == SOURCE


def test_suppressed_findings_are_not_fixed(tmp_path: Path) -> None:
    source = "# smelt: ignore-file[SMT313] -- legacy declaration order\n" + SOURCE
    root = _project(tmp_path, source=source)

    fixed = apply_ordering_fixes(check(root, OPTIONS))

    assert fixed.files == []
    assert (root / "app/service.py").read_text() == source


def test_accepted_debt_is_not_fixed_without_explicit_no_debt(tmp_path: Path) -> None:
    root = _project(tmp_path)
    initial = check(root, OPTIONS)
    baseline = Debt.from_violations(
        initial.report.violations, snippet_reader(initial.context)
    )
    baseline.write(root / "debt.json")
    write_project(root, {"smelt.yaml": CONFIG + "\ndebt: debt.json\n"})

    assert apply_ordering_fixes(check(root, OPTIONS)).files == []
    assert apply_ordering_fixes(
        check(root, CheckOptions(select=("SMT313",), use_debt=False))
    ).files == ["app/service.py"]


@pytest.mark.parametrize(
    ("source", "config", "reason"),
    [
        (SOURCE, CONFIG.replace("ordering: {}", "ordering: {fix: off}"), "disabled"),
        (
            "if __name__ == '__main__':\n    main()\ndef api(): pass\n",
            CONFIG,
            "main guard",
        ),
        ("# fmt: off\n" + SOURCE, CONFIG, "region"),
        (
            "class Service(Base):\n    def _helper(self): pass\n    def api(self): pass\n",
            CONFIG,
            "class bases",
        ),
    ],
)
def test_manual_review_cases_remain_unchanged(
    tmp_path: Path, source: str, config: str, reason: str
) -> None:
    root = _project(tmp_path, source=source, config=config)

    fixed = apply_ordering_fixes(check(root, OPTIONS))

    assert fixed.files == []
    assert any(reason in item["reason"] for item in fixed.skipped)
    assert (root / "app/service.py").read_text() == source


def test_user_edit_after_check_is_preserved(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outcome = check(root, OPTIONS)
    edited = SOURCE + "# User edit after analysis.\n"
    (root / "app/service.py").write_text(edited)

    fixed = apply_ordering_fixes(outcome)

    assert fixed.files == []
    assert "changed" in fixed.skipped[0]["reason"]
    assert (root / "app/service.py").read_text() == edited


def test_external_source_root_is_checked_but_never_rewritten(tmp_path: Path) -> None:
    config = CONFIG.replace(
        "root_packages: [app]", "root_packages: [app], source_roots: [../library/src]"
    )
    write_project(
        tmp_path, {"policy/smelt.yaml": config, "library/src/app/service.py": SOURCE}
    )

    fixed = apply_ordering_fixes(check(tmp_path / "policy", OPTIONS))

    assert fixed.files == []
    assert "inside the project root" in fixed.skipped[0]["reason"]
    assert (tmp_path / "library/src/app/service.py").read_text() == SOURCE


@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation requires Windows privileges"
)
def test_symlink_source_is_not_replaced(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "app/alias.py").symlink_to(root / "app/service.py")
    outcome = check(root, CheckOptions(select=("SMT313",), paths=("app/alias.py",)))

    fixed = apply_ordering_fixes(outcome)

    assert fixed.files == []
    assert (root / "app/alias.py").is_symlink()
    assert (root / "app/service.py").read_text() == SOURCE


def test_failed_replacement_leaves_original_and_cleans_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    outcome = check(root, OPTIONS)

    def fail(_path: Path, _target: Path) -> Path:
        raise PermissionError("target locked")

    monkeypatch.setattr(Path, "replace", fail)
    fixed = apply_ordering_fixes(outcome)

    assert fixed.files == []
    assert "locked" in fixed.skipped[0]["reason"]
    assert (root / "app/service.py").read_text() == SOURCE
    assert list((root / "app").glob(".smelt-order-*.tmp")) == []
