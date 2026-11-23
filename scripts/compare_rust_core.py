"""Compare the Rust core's findings with the Python engine, finding by finding.

Every project is copied to a temporary directory, so the originals stay untouched.
Each copy is checked at several limits to produce many findings. The parity
fixture also runs with CRLF line endings and a UTF-8 byte-order mark.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from smelt.config.loader import load_config
from smelt.engine.check import CheckOptions, run_check

REPOSITORY = Path(__file__).resolve().parent.parent
FIXTURE = REPOSITORY / "tests" / "fixtures" / "rust_parity"
PORTED = (
    "CPX003",
    "CPX005",
    "CPX006",
    "CPX007",
    "CPX008",
    "FWK001",
    "FWK002",
    "IDM002",
    "IDM003",
    "MOD003",
    "MOD007",
    "MOD011",
    "TYP007",
    "VIS007",
)
# Each limit rule with the smallest `max` its configuration accepts.
LIMIT_RULES = {
    "CPX003": ("complexity/excess-positional-parameters", 0),
    "CPX006": ("complexity/excess-comprehension-loops", 1),
    "CPX007": ("complexity/excess-constructor-decisions", 0),
    "VIS007": ("visibility/excess-private-definitions", 0),
}
LIMITS = (None, 0, 1, 2, 3)
_SKIPPED = shutil.ignore_patterns(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "target",
    "dist",
    "node_modules",
    ".claude",
    ".smelt_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*", type=Path)
    parser.add_argument(
        "--native",
        type=Path,
        default=Path(sys.executable).with_name(
            "smelt.exe" if os.name == "nt" else "smelt"
        ),
    )
    args = parser.parse_args()
    projects = [path.resolve() for path in args.projects] or [FIXTURE, REPOSITORY]
    native = args.native.resolve()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="smelt-parity-") as temporary:
        for index, project in enumerate(projects):
            variants = ("lf", "crlf", "bom") if project == FIXTURE else ("lf",)
            for variant in variants:
                copy = Path(temporary) / f"{index}-{variant}"
                shutil.copytree(project, copy, ignore=_SKIPPED)
                _rewrite_sources(copy, variant)
                for limit in LIMITS:
                    label = f"{project.name} {variant} limit={limit}"
                    failures += _compare(copy, limit, label=label, native=native)
    _emit("parity holds" if not failures else f"{failures} comparisons differ")
    return 1 if failures else 0


def _emit(line: str) -> None:
    sys.stdout.write(f"{line}\n")


def _rewrite_sources(root: Path, variant: str) -> None:
    if variant == "lf":
        return
    for path in root.rglob("*.py"):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(
            data.replace(b"\n", b"\r\n")
            if variant == "crlf"
            else b"\xef\xbb\xbf" + data
        )


def _compare(root: Path, limit: int | None, *, label: str, native: Path) -> int:
    config_path = root / "smelt.yml"
    original = config_path.read_text(encoding="utf-8")
    if limit is not None:
        raw = yaml.safe_load(original)
        rules = raw.setdefault("rules", {})
        for code, (name, minimum) in LIMIT_RULES.items():
            rules.pop(code, None)
            rules[name] = {"severity": "warning", "max": max(limit, minimum)}
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    try:
        loaded = load_config(config_path)
        started = time.perf_counter()
        outcome = run_check(
            loaded, CheckOptions(select=PORTED, use_cache=False, use_debt=False)
        )
        python_seconds = time.perf_counter() - started
        expected = sorted(
            (
                violation.to_json()
                for violation in outcome.report.violations
                if violation.code in PORTED
            ),
            key=_order,
        )
        actual, native_seconds = _native_findings(config_path, native)
    finally:
        config_path.write_text(original, encoding="utf-8")
    differences = _differences(expected, actual)
    _emit(
        f"{label}: {len(expected)} findings, python {python_seconds:.2f}s, "
        f"rust {native_seconds * 1000:.0f}ms, "
        f"{'match' if not differences else f'{len(differences)} DIFFER'}"
    )
    for difference in differences[:5]:
        _emit(f"  {difference}")
    return 1 if differences else 0


def _native_findings(
    config_path: Path, native: Path
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    result = subprocess.run(  # noqa: S603 - the locally built client
        [str(native), "--internal-core", str(config_path), ",".join(PORTED)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    seconds = time.perf_counter() - started
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return sorted(json.loads(result.stdout)["violations"], key=_order), seconds


def _order(finding: dict[str, Any]) -> tuple[str, int, int, str, str]:
    return (
        finding["path"] or "",
        finding["line"] or 0,
        finding["column"] or 0,
        finding["code"],
        finding["message"],
    )


def _differences(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> list[str]:
    left = {finding["id"]: finding for finding in expected}
    right = {finding["id"]: finding for finding in actual}
    found = [
        f"only python: {left[key]['path']}:{left[key]['line']} {left[key]['message']}"
        for key in left.keys() - right.keys()
    ]
    found.extend(
        f"only rust: {right[key]['path']}:{right[key]['line']} {right[key]['message']}"
        for key in right.keys() - left.keys()
    )
    for key in left.keys() & right.keys():
        fields = [
            field
            for field in left[key].keys() | right[key].keys()
            if left[key].get(field) != right[key].get(field)
        ]
        if fields:
            detail = {
                field: (left[key].get(field), right[key].get(field)) for field in fields
            }
            found.append(f"{left[key]['path']}:{left[key]['line']} {detail}")
    return found


if __name__ == "__main__":
    raise SystemExit(main())
