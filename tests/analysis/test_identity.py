from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from smelt.analysis import identity
from smelt.analysis.cache import FactCache

if TYPE_CHECKING:
    from collections.abc import Iterator

REPOSITORY = Path(__file__).resolve().parents[2]
_POLICY = yaml.safe_load((REPOSITORY / "smelt.yml").read_text(encoding="utf-8"))
DEPENDENCIES: dict[str, list[str]] = {
    name: layer["may_depend_on"]
    for name, layer in _POLICY["architecture"]["layers"].items()
}
DIRECTORIES: dict[str, str] = {
    name: layer["path"] for name, layer in _POLICY["architecture"]["layers"].items()
}


def _reachable(layer: str) -> set[str]:
    """A layer and everything smelt.yml lets it import, directly or not."""
    reachable = {layer}
    pending = [layer]
    while pending:
        for dependency in DEPENDENCIES[pending.pop()]:
            if dependency not in reachable:
                reachable.add(dependency)
                pending.append(dependency)
    return reachable


@pytest.fixture(autouse=True)
def reset_identity() -> Iterator[None]:
    identity.analyzer_identity.cache_clear()
    yield
    identity.analyzer_identity.cache_clear()


def install(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    package = root / "smelt"
    package.mkdir(parents=True)
    (package / "value.py").write_text("VALUE = 1\n")
    metadata = root / "smelt_cli-0.1.6.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: smelt-cli\nVersion: 0.1.6\n")
    record = metadata / "RECORD"
    record.write_text(
        "smelt/value.py,sha256="
        + base64.urlsafe_b64encode(hashlib.sha256(b"VALUE = 1\n").digest()).decode()
        + ",10\n"
    )
    installed = importlib.metadata.Distribution.at(metadata)
    monkeypatch.setattr(identity, "distribution", lambda name: installed)
    monkeypatch.setattr(identity, "__file__", str(package / "analysis/identity.py"))
    return package, metadata


def test_wheel_record_avoids_source_reads_and_reinstall_changes_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, metadata = install(tmp_path, monkeypatch)
    original = type(package).read_bytes

    def read(path: Path) -> bytes:
        if path.is_relative_to(package):
            pytest.fail("installed wheel identity should use its RECORD")
        return original(path)

    monkeypatch.setattr(type(package), "read_bytes", read)
    first = identity.analyzer_identity()
    (metadata / "RECORD").write_text("smelt/value.py,sha256=reinstalled,10\n")
    identity.analyzer_identity.cache_clear()
    assert identity.analyzer_identity() != first


def test_editable_source_changes_invalidate_even_with_same_size_and_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, metadata = install(tmp_path, monkeypatch)
    (metadata / "direct_url.json").write_text(
        json.dumps({"dir_info": {"editable": True}})
    )
    first = identity.analyzer_identity()
    source = package / "value.py"
    stat = source.stat()
    source.write_text("VALUE = 2\n")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    identity.analyzer_identity.cache_clear()
    assert identity.analyzer_identity() != first


def test_pythonpath_override_does_not_use_another_installations_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(tmp_path / "installed", monkeypatch)
    override = tmp_path / "override/smelt"
    override.mkdir(parents=True)
    source = override / "value.py"
    source.write_text("VALUE = 1\n")
    monkeypatch.setattr(identity, "__file__", str(override / "analysis/identity.py"))
    first = identity.analyzer_identity()
    source.write_text("VALUE = 2\n")
    identity.analyzer_identity.cache_clear()
    assert identity.analyzer_identity() != first


def test_rule_edit_preserves_fact_namespace_and_invalidates_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, metadata = install(tmp_path, monkeypatch)
    (metadata / "direct_url.json").write_text(
        json.dumps({"dir_info": {"editable": True}})
    )
    rules = package / "rules"
    rules.mkdir()
    rule = rules / "example.py"
    rule.write_text("LIMIT = 1")
    before = FactCache(tmp_path / "cache")
    first = before.decision_key("file-check", ["EX001", "input"])
    rule.write_text("LIMIT = 2")
    identity.analyzer_identity.cache_clear()
    after = FactCache(tmp_path / "cache")
    assert before.directory == after.directory
    assert after.decision_key("file-check", ["EX001", "input"]) != first
    (package / "value.py").write_text("VALUE = 2")
    identity.analyzer_identity.cache_clear()
    assert FactCache(tmp_path / "cache").directory != after.directory


@pytest.mark.parametrize("layer", sorted(DEPENDENCIES))
def test_an_edit_invalidates_only_what_may_import_its_layer(
    layer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Facts are built in analysis and decisions in the engine; smelt.yml bounds both."""
    package, metadata = install(tmp_path, monkeypatch)
    (metadata / "direct_url.json").write_text(
        json.dumps({"dir_info": {"editable": True}})
    )
    source = package / DIRECTORIES[layer] / "example.py"
    source.parent.mkdir()
    source.write_text("LIMIT = 1")
    before = FactCache(tmp_path / "cache")
    decision = before.decision_key("file-check", ["EX001", "input"])
    complete = identity.analyzer_identity()

    source.write_text("LIMIT = 2")
    identity.analyzer_identity.cache_clear()
    after = FactCache(tmp_path / "cache")

    assert (after.directory != before.directory) == (layer in _reachable("analysis"))
    assert (after.decision_key("file-check", ["EX001", "input"]) != decision) == (
        layer in _reachable("engine")
    )
    assert identity.analyzer_identity() != complete


def test_installed_wheel_keeps_facts_across_a_decision_layer_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, metadata = install(tmp_path, monkeypatch)
    # The record only lists files that exist; their hashes come from the record.
    (package / "engine").mkdir()
    (package / "engine/check.py").write_text("")
    record = metadata / "RECORD"
    value = record.read_text()
    record.write_text(value + "smelt/engine/check.py,sha256=AAAA,4\n")
    before = FactCache(tmp_path / "cache")
    first = before.decision_key("file-check", ["EX001", "input"])

    record.write_text(value + "smelt/engine/check.py,sha256=BBBB,4\n")
    identity.analyzer_identity.cache_clear()
    after = FactCache(tmp_path / "cache")

    assert after.directory == before.directory
    assert after.decision_key("file-check", ["EX001", "input"]) != first


def test_layers_outside_the_facts_do_not_store_plain_keyed_entries() -> None:
    """Facts survive edits to these layers, so their entries need a decision key."""
    plain = re.compile(r"cache\.(?:get|key)\(\s*\"([a-z-]+)\"")
    found = {
        (source.relative_to(REPOSITORY / "smelt").as_posix(), kind)
        for layer in sorted(set(DEPENDENCIES) - _reachable("analysis"))
        for source in (REPOSITORY / "smelt" / DIRECTORIES[layer]).rglob("*.py")
        for kind in plain.findall(source.read_text(encoding="utf-8"))
    }

    assert found == set()


def test_layers_above_the_engine_do_not_store_decisions() -> None:
    """Decisions survive edits to these layers, so they may not write any."""
    found = [
        source.relative_to(REPOSITORY / "smelt").as_posix()
        for layer in sorted(set(DEPENDENCIES) - _reachable("engine"))
        for source in (REPOSITORY / "smelt" / DIRECTORIES[layer]).rglob("*.py")
        if "decision_key(" in source.read_text(encoding="utf-8")
    ]

    assert found == []
