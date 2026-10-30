"""Cache identity without importing the analysis engine or its schemas."""

import hashlib
import json
import sys
from functools import cache
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path


def _installed_sources(package: Path) -> list[tuple[str, str, str, int | None]] | None:
    """Use the installed wheel's build hashes, never those of another checkout."""
    try:
        installed = distribution("smelt-cli")
        direct = json.loads(installed.read_text("direct_url.json") or "{}")
        if direct.get("dir_info", {}).get("editable", False):
            return None
        if Path(str(installed.locate_file("smelt"))).resolve() != package:
            return None
        files = sorted(
            (
                file
                for file in installed.files or ()
                if file.parts and file.parts[0] == "smelt" and file.suffix == ".py"
            ),
            key=str,
        )
        if not files or any(file.hash is None for file in files):
            return None
        return [
            (str(file), file.hash.mode, file.hash.value, file.size)
            for file in files
            if file.hash is not None
        ]
    except (OSError, ValueError, TypeError, AttributeError, PackageNotFoundError):
        return None


@cache
def analyzer_identity() -> str:
    """Invalidate editable checkouts too, even without a package version bump."""
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent.parent
    record = _installed_sources(package)
    if record is not None:
        digest.update(b"wheel-record\0")
        digest.update(json.dumps(record).encode())
    else:
        digest.update(b"source\0")
        for path in sorted(package.rglob("*.py")):
            digest.update(path.relative_to(package).as_posix().encode())
            digest.update(path.read_bytes())
    digest.update(str(sys.version_info).encode())
    for dependency in ("smelt-cli", "grimp", "pydantic", "packaging", "pyyaml"):
        try:
            installed = version(dependency)
        except PackageNotFoundError:
            installed = "uninstalled"
        digest.update(f"{dependency}:{installed}".encode())
    return digest.hexdigest()
