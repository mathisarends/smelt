from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from smelt.cli import main
from smelt.diagnostics.debt import Debt, DebtEntry
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("version", [1, 2])
def test_recording_groups_importers_and_pruning_preserves_only_accepted_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version: int,
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n"
            "structure:\n  contracts:\n    api: {match: app.api, export_at: 2}\n"
            "rules: {SMT109: warning, SMT104: off}\ndebt: debt.json\n",
            "app/__init__.py": "",
            "app/api/__init__.py": "",
            "app/api/item.py": "class Item: pass\n",
            **{
                f"app/{name}.py": "from app.api.item import Item\n"
                for name in ("a", "b", "c")
            },
        },
    )
    monkeypatch.chdir(root)
    assert main(["debt"]) == 0
    assert "1 violation, 3 matching entries" in capsys.readouterr().out
    path = root / "debt.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["violations"]) == 1
    assert len(data["violations"][0]["fingerprints"]) == 3
    baseline = Debt.load(path)
    dormant = DebtEntry("inactive", "SMT104", "app/old.py", "old cycle")
    baseline.entries.append(dormant)
    if version == 1:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "violations": [
                        {
                            "fingerprint": item.fingerprint,
                            "code": item.code,
                            "path": item.path,
                            "message": item.message,
                        }
                        for item in baseline.entries
                    ],
                }
            ),
            encoding="utf-8",
        )
    else:
        baseline.write(path)
    write_project(root, {"app/b.py": "", "app/d.py": "from app.api.item import Item\n"})
    assert main(["debt", "--prune"]) == 0
    output = capsys.readouterr()
    assert not output.err
    assert "Removed 1 resolved entry" in output.out
    pruned = Debt.load(path)
    assert len(pruned.entries) == 3
    assert dormant in pruned.entries
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2
    [finding] = check(root, CheckOptions(select=("SMT109",))).report.violations
    assert finding.code == "SMT109"


@pytest.mark.parametrize("version", [1, 2])
def test_show_filters_stored_debt_without_analyzing_or_rewriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version: int,
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\ndebt: debt.json\n",
            # Showing the baseline must work with missing/broken analyzed sources.
            "app/broken.py": "def (\n",
        },
    )
    entries = [
        DebtEntry("a", "SMT109", "app/api/item.py", "export Item", "app.api.item.Item"),
        DebtEntry("b", "SMT109", "app/api/item.py", "export Item", "app.api.item.Item"),
        DebtEntry("c", "SMT313", "app/other.py", "order definitions"),
    ]
    path = root / "debt.json"
    Debt(entries).write(path)
    if version == 1:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "violations": [
                        {
                            "fingerprint": item.fingerprint,
                            "code": item.code,
                            "path": item.path,
                            "message": item.message,
                        }
                        for item in entries
                    ],
                }
            ),
            encoding="utf-8",
        )
    original = path.read_bytes()
    monkeypatch.chdir(root)
    assert main(["debt", "--show", "app/api"]) == 0
    output = capsys.readouterr()
    assert not output.err
    assert "1 group, 2 matching entries" in output.out
    assert "SMT109 app/api/item.py" in output.out
    assert "export Item" in output.out
    assert "SMT313" not in output.out
    assert '"fingerprints"' not in output.out
    assert main(["debt", "--show", "app/api/item.py", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == 2
    assert data["violations"][0]["fingerprints"] == ["a", "b"]
    monkeypatch.chdir(root / "app")
    assert main(["debt", "--show", "api", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == data
    assert path.read_bytes() == original
    assert not (root / ".smelt_cache").exists()


def test_show_missing_baseline_is_empty_and_does_not_enable_debt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = "version: 1\nproject: {root_packages: [app]}\n"
    write_project(tmp_path, {"smelt.yml": config})
    monkeypatch.chdir(tmp_path)
    assert main(["debt", "--show", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"version": 2, "violations": []}
    assert (tmp_path / "smelt.yml").read_text(encoding="utf-8") == config
    assert not (tmp_path / ".smelt").exists()


@pytest.mark.parametrize("args", [["--show", "--prune"], ["app"], ["--format", "json"]])
def test_invalid_inspection_arguments_never_write_debt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
) -> None:
    write_project(
        tmp_path, {"smelt.yml": "version: 1\nproject: {root_packages: [app]}\n"}
    )
    monkeypatch.chdir(tmp_path)
    assert main(["debt", *args]) == 2
    output = capsys.readouterr()
    assert output.err or json.loads(output.out)["status"] == "error"
    assert not (tmp_path / ".smelt").exists()
