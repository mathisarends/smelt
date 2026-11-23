"""Compare the Rust core's findings with the Python engine, finding by finding.

Every project is copied to a temporary directory, so the originals stay untouched.
Each copy is checked at several limits to produce many findings. The parity
fixture also runs with CRLF line endings, a UTF-8 byte-order mark and without a
final newline.
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
    "CPX002",
    "CPX003",
    "CPX005",
    "CPX006",
    "CPX007",
    "CPX008",
    "FWK001",
    "FWK002",
    "IDM001",
    "IDM002",
    "IDM003",
    "IDM004",
    "IDM005",
    "IDM006",
    "IDM007",
    "IDM008",
    "LAY002",
    "LAY006",
    "LAY008",
    "LAY010",
    "LAY011",
    "MOD003",
    "MOD004",
    "MOD005",
    "MOD006",
    "MOD007",
    "MOD008",
    "MOD010",
    "MOD011",
    "MOD012",
    "TST002",
    "TST003",
    "TST004",
    "TST005",
    "TST006",
    "TST007",
    "TST008",
    "TST009",
    "TST010",
    "TST011",
    "TYP001",
    "TYP002",
    "TYP003",
    "TYP004",
    "TYP006",
    "TYP007",
    "TYP008",
    "VIS003",
    "VIS006",
    "VIS007",
    "VIS008",
)
# Each limit rule with the smallest `max` its configuration accepts.
LIMIT_RULES = {
    "CPX003": ("complexity/excess-positional-parameters", 0),
    "CPX006": ("complexity/excess-comprehension-loops", 1),
    "CPX007": ("complexity/excess-constructor-decisions", 0),
    "TST005": ("tests/excess-patches", 0),
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
            variants = (
                ("lf", "crlf", "bom", "no-final-newline")
                if project == FIXTURE
                else ("lf",)
            )
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
        if variant == "crlf":
            path.write_bytes(data.replace(b"\n", b"\r\n"))
        elif variant == "bom":
            path.write_bytes(b"\xef\xbb\xbf" + data)
        else:
            path.write_bytes(data.rstrip(b"\n"))


def _compare(root: Path, limit: int | None, *, label: str, native: Path) -> int:
    config_path = root / "smelt.yml"
    original = config_path.read_text(encoding="utf-8")
    if limit is not None:
        raw = yaml.safe_load(original)
        rules = raw.setdefault("rules", {})
        for code, (name, minimum) in LIMIT_RULES.items():
            rules.pop(code, None)
            rules[name] = {"severity": "warning", "max": max(limit, minimum)}
        # MOD008 only runs under an error policy, which real projects may not set.
        raw.setdefault("style", {}).setdefault("errors", {"base": "project.Error"})
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
    left = _by_id(expected)
    right = _by_id(actual)
    found = [
        f"only python: {left[key][0]['path']}:{left[key][0]['line']} "
        f"{left[key][0]['message']}"
        for key in left.keys() - right.keys()
    ]
    found.extend(
        f"only rust: {right[key][0]['path']}:{right[key][0]['line']} "
        f"{right[key][0]['message']}"
        for key in right.keys() - left.keys()
    )
    for key in left.keys() & right.keys():
        first = left[key][0]
        if len(left[key]) != len(right[key]):
            found.append(
                f"{first['path']}:{first['line']} {first['code']}: "
                f"{len(left[key])} in python, {len(right[key])} in rust"
            )
            continue
        for python, rust in zip(left[key], right[key], strict=True):
            fields = [
                field
                for field in python.keys() | rust.keys()
                if python.get(field) != rust.get(field)
            ]
            if fields:
                detail = {
                    field: (python.get(field), rust.get(field)) for field in fields
                }
                found.append(f"{python['path']}:{python['line']} {detail}")
    return found


def _by_id(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Findings by id; repeated ids stay, in an order independent of the engine."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(finding["id"], []).append(finding)
    for group in grouped.values():
        group.sort(key=lambda finding: json.dumps(finding, sort_keys=True))
    return grouped


if __name__ == "__main__":
    raise SystemExit(main())
