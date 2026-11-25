"""Requests to the native core, which the installed smelt launcher serves."""

import json
import os
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from typing import Any

from smelt.analysis.errors import AnalysisError


def request(flag: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The JSON the core prints for `payload` in the mode `flag` selects.

    With `SMELT_PROFILE` set, the core's phase timings reach stderr.
    """
    started = time.perf_counter()
    result = subprocess.run(  # noqa: S603 - the installed native launcher
        [_executable(), flag],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        # The core reads installed packages from this interpreter's environment.
        env={**os.environ, "VIRTUAL_ENV": sys.prefix},
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if os.environ.get("SMELT_PROFILE"):
        elapsed = (time.perf_counter() - started) * 1000
        sys.stderr.write(result.stderr)
        sys.stderr.write(f"smelt profile: {flag} took {elapsed:.1f} ms in total\n")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise AnalysisError(f"the native core failed: {detail}") from None
    if not isinstance(data, dict):
        raise AnalysisError("the native core printed no JSON object")
    return data


def _executable() -> str:
    """The native launcher that started this process, else the installed one."""
    started = os.environ.get("SMELT_NATIVE_CLIENT")
    if started:
        return started
    name = "smelt.exe" if os.name == "nt" else "smelt"
    installed = Path(sysconfig.get_path("scripts")) / name
    if installed.is_file():
        return str(installed)
    msg = "checks need the native smelt launcher, which is not installed"
    raise AnalysisError(msg)
