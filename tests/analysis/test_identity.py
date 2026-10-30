from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
from typing import TYPE_CHECKING

import pytest

from smelt.analysis import identity

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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
