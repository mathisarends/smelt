"""Cache identity without importing the analysis engine or its schemas."""

import hashlib
import sys
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


@cache
def analyzer_identity() -> str:
    """Invalidate editable checkouts too, even without a package version bump."""
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent.parent
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
