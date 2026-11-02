from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.debt import Debt, DebtEntry
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("version", [1, 2])
def test_grouped_roundtrip_keeps_duplicate_tokens_and_separate_scopes(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "debt.json"
    items = [
        {
            "fingerprint": "same",
            "code": "SMT321",
            "path": "app/a.py",
            "message": "default",
        },
        {
            "fingerprint": "same",
            "code": "SMT321",
            "path": "app/a.py",
            "message": "default",
        },
        {
            "fingerprint": "other",
            "code": "SMT313",
            "path": "app/a.py",
            "message": "order",
        },
    ]
    path.write_text(json.dumps({"version": 1, "violations": items}), encoding="utf-8")
    debt = Debt.load(path)
    if version == 2:
        debt.entries.append(
            DebtEntry("scoped", "SMT321", "app/a.py", "default", "Client.create")
        )
    expected = Counter(debt.entries)
    debt.write(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert len(data["violations"]) == (3 if version == 2 else 2)
    [duplicates] = [
        item for item in data["violations"] if item["fingerprints"] == ["same", "same"]
    ]
    assert duplicates["message"] == "default"
    assert Counter(Debt.load(path).entries) == expected
    first_write = path.read_bytes()
    Debt(list(reversed(debt.entries))).write(path)
    assert path.read_bytes() == first_write


@pytest.mark.parametrize(
    "group",
    [
        {"code": "SMT109", "fingerprints": []},
        {"code": "SMT109", "fingerprints": [7]},
        {"code": "SMT109", "fingerprints": ["ok"], "unknown": True},
        {"code": "SMT109", "fingerprint": "singular"},
    ],
)
def test_malformed_grouped_baselines_fail_with_file_context(
    tmp_path: Path, group: dict[str, object]
) -> None:
    path = tmp_path / "debt.json"
    path.write_text(json.dumps({"version": 2, "violations": [group]}), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"debt\.json"):
        Debt.load(path)


@pytest.mark.parametrize("code", ["SMT109", "SMT313"])
def test_grouped_edges_detect_replacement_and_prune_without_accepting_it(
    tmp_path: Path, code: str
) -> None:
    if code == "SMT109":
        config = "structure:\n  contracts:\n    api: {match: app.api, export_at: 2}\n"
        files = {
            "app/api/__init__.py": "",
            "app/api/item.py": "class Item: pass\n",
            **{
                f"app/{name}.py": "from app.api.item import Item\n"
                for name in ("a", "b", "c")
            },
        }
        changed = {"app/b.py": "", "app/d.py": "from app.api.item import Item\n"}
    else:
        config = "structure:\n  contracts:\n    order: {match: app, ordering: {}, rules: {SMT313: error}}\n"
        files = {
            "app/item.py": "def _helper(): pass\ndef first(): pass\ndef second(): pass\n"
        }
        changed = {
            "app/item.py": "def _helper(): pass\ndef first(): pass\ndef third(): pass\n"
        }
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\ndebt: debt.json\n"
            + config,
            "app/__init__.py": "",
            **files,
        },
    )
    options = CheckOptions(select=(code,))
    original = check(root, options)
    debt = Debt.from_violations(original.unfiltered, snippet_reader(original.context))
    debt.write(root / "debt.json")
    data = json.loads((root / "debt.json").read_text(encoding="utf-8"))
    assert len(data["violations"]) == 1
    assert len(data["violations"][0]["fingerprints"]) == (3 if code == "SMT109" else 2)
    assert check(root, options).report.violations == []
    write_project(root, changed)
    current = check(root, options)
    assert [finding.code for finding in current.report.violations] == [code]
    assert len(current.resolved_debt) == 1
    _, known, resolved = debt.match(current.unfiltered, snippet_reader(current.context))
    pruned = Debt.from_violations(known, snippet_reader(current.context))
    assert len(pruned.entries) == len(debt.entries) - len(resolved)
    pruned.write(root / "debt.json")
    assert [finding.code for finding in check(root, options).report.violations] == [
        code
    ]


def test_identical_optional_occurrences_keep_their_multiplicity(tmp_path: Path) -> None:
    source = "def create(value: str | None = None):\n    one = value or 'default'\n    one = value or 'default'\n"
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\ndebt: debt.json\n",
            "app/__init__.py": "",
            "app/item.py": source,
        },
    )
    options = CheckOptions(select=("SMT321",))
    original = check(root, options)
    debt = Debt.from_violations(original.unfiltered, snippet_reader(original.context))
    debt.write(root / "debt.json")
    assert len(debt.entries) == 2
    assert len({entry.fingerprint for entry in debt.entries}) == 1
    write_project(root, {"app/item.py": source + "    one = value or 'default'\n"})
    current = check(root, options)
    assert len(current.report.violations) == 1
    assert current.report.in_debt == 2
