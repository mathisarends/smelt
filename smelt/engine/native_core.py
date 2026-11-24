"""The analysis of a check in the native core, which `SMELT_CORE=native` selects.

The core discovers, parses and judges the project itself and returns the
findings with the coverage facts a report needs. Checks of a Git snapshot
(`--staged`, `--changed`) still analyze in Python.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.analysis.contracts import ResolvedContract, describe_contract
from smelt.analysis.errors import AnalysisError
from smelt.diagnostics.violation import Violation
from smelt.engine.cache import CachedAnalysis
from smelt.engine.coverage import effective_coverage

if TYPE_CHECKING:
    from smelt.config.loader import LoadedConfig

_VIOLATIONS = TypeAdapter(list[Violation])


def selected() -> bool:
    """Whether this process analyzes checks in the native core."""
    return os.environ.get("SMELT_CORE") == "native"


def analyze(
    loaded: LoadedConfig,
    *,
    select: tuple[str, ...],
    ignore: tuple[str, ...],
    strict_coverage: bool,
    configuration_findings: bool,
) -> CachedAnalysis:
    """The findings of one check and the coverage its report describes.

    `select` and `ignore` hold rule codes. The core reads the configuration from
    its file; the loaded one completes the contracts the core resolved and
    describes the policy of the coverage.
    """
    request = {
        "config": str(loaded.path),
        "select": list(select),
        "ignore": list(ignore),
        "strict_coverage": strict_coverage,
        "configuration_findings": configuration_findings,
    }
    result = subprocess.run(  # noqa: S603 - the installed native launcher
        [_executable(), "--internal-check"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        # The core reads installed packages from this interpreter's environment.
        env={**os.environ, "VIRTUAL_ENV": sys.prefix},
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        data = json.loads(result.stdout)
    except ValueError:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise AnalysisError(f"the native core failed: {detail}") from None
    if "error" in data:
        raise AnalysisError(data["error"])
    facts = data["coverage"]
    facts["contracts"] = [
        describe_contract(
            ResolvedContract(
                entry["name"],
                entry["package"],
                loaded.config.structure.contracts[entry["name"]],
            ),
            missing=entry["missing"],
            missing_public=entry["missing_public"],
        )
        for entry in facts["contracts"]
    ]
    return CachedAnalysis(
        _VIOLATIONS.validate_python(data["violations"]),
        data["suppressed"],
        effective_coverage(loaded.config, facts, include_inventory=True),
        data["modules"],
        data["paths"],
    )


def _executable() -> str:
    """The native launcher that started this process, else the installed one."""
    started = os.environ.get("SMELT_NATIVE_CLIENT")
    if started:
        return started
    name = "smelt.exe" if os.name == "nt" else "smelt"
    installed = Path(sysconfig.get_path("scripts")) / name
    if installed.is_file():
        return str(installed)
    msg = "SMELT_CORE=native needs the native smelt launcher, which is not installed"
    raise AnalysisError(msg)
