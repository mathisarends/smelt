"""The analysis of a check in the native core, which checks use by default.

The core discovers, parses and judges the project itself and returns the
findings with the coverage facts a report needs. It also checks the staged Git
index and a base revision, which it writes to a temporary directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from smelt.analysis.contracts import ResolvedContract, describe_contract
from smelt.analysis.errors import AnalysisError
from smelt.config.errors import ConfigError, ConfigIssue
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Violation
from smelt.engine.analysis import Analysis
from smelt.engine.coverage import effective_coverage
from smelt.engine.git_source import GitSourceStore

if TYPE_CHECKING:
    from smelt.config.loader import LoadedConfig

_VIOLATIONS = TypeAdapter(list[Violation])


@dataclass(frozen=True, slots=True)
class NativeAnalysis:
    analysis: Analysis
    # The findings the base revision already had, when the run compares with one.
    baseline: Debt | None = None
    # The SHA-256 of each text a fixable finding was found in, by path.
    digests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Baseline:
    """The commit a `--changed` check compares with."""

    repository: Path
    revision: str


@dataclass(frozen=True, slots=True)
class NativeRun:
    """What one check asks of the core; `select` and `ignore` hold rule codes."""

    select: tuple[str, ...]
    ignore: tuple[str, ...]
    strict_coverage: bool
    # Judge the configuration itself (MET004, MET005); a scoped run does not.
    configuration_findings: bool
    baseline: Baseline | None = None


def selected() -> bool:
    """Whether this process analyzes checks in the native core.

    `SMELT_CORE=python` selects the Python engine, which parity checks compare with.
    """
    return os.environ.get("SMELT_CORE") != "python"


def analyze(loaded: LoadedConfig, run: NativeRun) -> NativeAnalysis:
    """The findings of one check and the coverage its report describes.

    The core reads the configuration from
    its file, or from the staged index when `loaded` came from there; the loaded
    one completes the contracts the core resolved and describes the policy of
    the coverage. With a baseline, the core also checks that commit with the
    same configuration.
    """
    staged = loaded.source if isinstance(loaded.source, GitSourceStore) else None
    request = {
        "config": str(loaded.path),
        "select": list(run.select),
        "ignore": list(run.ignore),
        "strict_coverage": run.strict_coverage,
        "configuration_findings": run.configuration_findings,
        "index": (
            {"repository": str(staged.repository), "blobs": staged.blobs}
            if staged
            else None
        ),
        "baseline": (
            {
                "repository": str(run.baseline.repository),
                "revision": run.baseline.revision,
            }
            if run.baseline
            else None
        ),
        # Installed packages are found where this interpreter imports them from.
        "search_path": sys.path,
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
    if "config" in data:
        raise _config_error(data["config"])
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
    analysis = Analysis(
        _VIOLATIONS.validate_python(data["violations"]),
        data["suppressed"],
        effective_coverage(loaded.config, facts, include_inventory=True),
        data["modules"],
        data["paths"],
    )
    return NativeAnalysis(analysis, _known(data["baseline"]), data["digests"])


def _config_error(error: dict[str, Any]) -> ConfigError:
    """The configuration error the core reported, as the Python engine raises it."""
    issues = [
        ConfigIssue(issue["path"], issue["message"], issue["source"])
        for issue in error["issues"]
    ]
    return ConfigError(issues, error["source"])


def _known(findings: list[dict[str, object]] | None) -> Debt | None:
    """The baseline findings as debt, fingerprinted by the lines the core read."""
    if findings is None:
        return None
    lines = [str(finding.pop("snippet")) for finding in findings]
    violations = _VIOLATIONS.validate_python(findings)
    by_identity = {
        id(violation): line for violation, line in zip(violations, lines, strict=True)
    }
    return Debt.from_violations(
        violations, lambda violation: by_identity[id(violation)]
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
    msg = "checks need the native smelt launcher, which is not installed"
    raise AnalysisError(msg)
