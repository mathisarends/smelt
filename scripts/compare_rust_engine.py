"""Compare whole check reports from the Python engine and the native core.

Every project is copied to a temporary directory and checked in several modes,
once with each engine. The JSON reports, coverage included, must be equal; a
check that fails must fail with the same error; only the parser's explanation
of a syntax error differs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from compare_rust_core import (
    _GUIDE_REF,
    _SKIPPED,
    ARCHITECTURE_FIXTURES,
    FIXTURE,
    REPOSITORY,
)

from smelt.config.loader import load_config
from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions, run_check

# CPython and the native parser explain a syntax error in their own words.
_SYNTAX_ERROR = re.compile(r"(: syntax error): .*", re.DOTALL)
SYNTAX_FIXTURE = REPOSITORY / "tests" / "fixtures" / "rust_parity_syntax_error"
BASE = CheckOptions(use_cache=False, show_coverage=True)
MODES = {
    "configured": BASE,
    "all rules": replace(BASE, select=("ALL",)),
    "strict coverage": replace(BASE, select=("ALL",), strict_coverage=True),
    "ignored": replace(BASE, select=("ALL",), ignore=("ARC", "TST")),
    "scoped": replace(BASE, paths=(".",)),
    "without debt": replace(BASE, use_debt=False),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*", type=Path)
    parser.add_argument("--native", type=Path, required=True)
    args = parser.parse_args()
    projects = [path.resolve() for path in args.projects] or [
        FIXTURE,
        *ARCHITECTURE_FIXTURES,
        SYNTAX_FIXTURE,
    ]
    os.environ["SMELT_NATIVE_CLIENT"] = str(args.native.resolve())
    failures = 0
    with tempfile.TemporaryDirectory(prefix="smelt-engine-") as temporary:
        for index, project in enumerate(projects):
            copy = Path(temporary) / str(index)
            shutil.copytree(project, copy, ignore=_SKIPPED)
            for mode, options in MODES.items():
                failures += _compare(copy, options, label=f"{project.name} {mode}")
    _emit("parity holds" if not failures else f"{failures} comparisons differ")
    return 1 if failures else 0


def _compare(root: Path, options: CheckOptions, *, label: str) -> int:
    expected = _report(root, options, engine="python")
    actual = _report(root, options, engine="native")
    if expected == actual:
        count = len(expected.get("violations", ()))
        _emit(f"MATCH  {label}: {count} findings")
        return 0
    _emit(f"DIFFER {label}")
    for line in _differences(expected, actual):
        _emit(f"  {line}")
    return 1


def _report(root: Path, options: CheckOptions, *, engine: str) -> dict[str, Any]:
    if engine == "native":
        os.environ["SMELT_CORE"] = "native"
    else:
        os.environ.pop("SMELT_CORE", None)
    try:
        outcome = run_check(load_config(root / "smelt.yml"), options)
    except Exception as error:
        message = _SYNTAX_ERROR.sub(r"\1", str(error))
        return {"error": f"{type(error).__name__}: {message}"}
    finally:
        os.environ.pop("SMELT_CORE", None)
    # Guide links name the release the native core was built for.
    text = _GUIDE_REF.sub(
        "/blob/REF/docs/upgrading", render_json(outcome.report, show_coverage=True)
    )
    report = json.loads(text)
    # Findings at one position keep no order between engines.
    report["violations"] = sorted(
        report.get("violations", ()), key=lambda item: json.dumps(item, sort_keys=True)
    )
    return report


def _differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        lines = []
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                lines.append(f"{path}.{key}: only python")
            elif key not in expected:
                lines.append(f"{path}.{key}: only native")
            else:
                lines.extend(_differences(expected[key], actual[key], f"{path}.{key}"))
        return lines
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [
                f"{path}: {len(expected)} python, {len(actual)} native",
                *(
                    f"  python only: {json.dumps(item)[:300]}"
                    for item in expected
                    if item not in actual
                ),
                *(
                    f"  native only: {json.dumps(item)[:300]}"
                    for item in actual
                    if item not in expected
                ),
            ][:40]
        lines = []
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            lines.extend(_differences(left, right, f"{path}[{index}]"))
        return lines[:40]
    if expected != actual:
        return [f"{path}: python {expected!r:.200} native {actual!r:.200}"]
    return []


def _emit(line: str) -> None:
    sys.stdout.write(f"{line}\n")


if __name__ == "__main__":
    sys.exit(main())
