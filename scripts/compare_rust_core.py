"""Compare the Rust core's findings with the Python engine, finding by finding.

Every project is copied to a temporary directory, so the originals stay untouched.
Each copy is checked at several limits to produce many findings. The parity
fixture also runs with CRLF line endings, a UTF-8 byte-order mark and without a
final newline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from smelt.analysis.context import AnalysisContext
from smelt.config.loader import load_config
from smelt.engine.check import CheckOptions, run_check

if TYPE_CHECKING:
    from collections.abc import Iterable

    from smelt.analysis.imports import ImportDetail

REPOSITORY = Path(__file__).resolve().parent.parent
FIXTURE = REPOSITORY / "tests" / "fixtures" / "rust_parity"
PORTED = (
    "ARC010",
    "ARC009",
    "IMP001",
    "ARC008",
    "ARC007",
    "ARC001",
    "ARC002",
    "ARC003",
    "ARC004",
    "ARC005",
    "ARC006",
    "ARC011",
    "ARC013",
    "ARC012",
    "ARC014",
    "CPX001",
    "CPX002",
    "CPX003",
    "CPX004",
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
    "IMP002",
    "IMP003",
    "IMP004",
    "LAY001",
    "LAY002",
    "LAY003",
    "LAY004",
    "LAY005",
    "LAY006",
    "LAY007",
    "LAY008",
    "LAY009",
    "LAY010",
    "LAY011",
    "MET001",
    "MET002",
    "MET004",
    "MET005",
    "MOD001",
    "MOD002",
    "MOD003",
    "MOD004",
    "MOD005",
    "MOD006",
    "MOD007",
    "MOD008",
    "MOD009",
    "MOD010",
    "MOD011",
    "MOD012",
    "TST001",
    "TST012",
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
    "TYP005",
    "TYP006",
    "TYP007",
    "TYP008",
    "VIS001",
    "VIS002",
    "VIS003",
    "VIS004",
    "VIS005",
    "VIS006",
    "VIS007",
    "VIS008",
    "VIS009",
    "VIS010",
)
# Each limit rule with the smallest `max` its configuration accepts.
LIMIT_RULES = {
    "CPX003": ("complexity/excess-positional-parameters", 0),
    "CPX006": ("complexity/excess-comprehension-loops", 1),
    "CPX007": ("complexity/excess-constructor-decisions", 0),
    "IMP002": ("imports/excess-module-imports", 0),
    "TST005": ("tests/excess-patches", 0),
    "VIS007": ("visibility/excess-private-definitions", 0),
}
LIMITS = (None, 0, 1, 2, 3)
ARCHITECTURE_FIXTURES = (
    REPOSITORY / "tests" / "fixtures" / "rust_parity_architecture",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_layers",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_import_style",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_enum_shadow",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_package_imports",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_navigation",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_private_access",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_members",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_test_location",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_test_location_root",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_contracts",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_workspace",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_architecture_scopes",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_coverage",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_coverage_workspace",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_untested",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_suppressions",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_policy_meta",
    REPOSITORY / "tests" / "fixtures" / "rust_parity_member_configs",
)
# Every way the import graph options change what the dependency rules see.
GRAPH_POLICIES = tuple(
    {
        "transitive": transitive,
        "type_checking": type_checking,
        "type_checking_cycles": type_checking_cycles,
        "cycles": ["features", "layers", "siblings", "modules"],
    }
    for transitive in (False, True)
    for type_checking in ("include", "ignore")
    for type_checking_cycles in ("include", "ignore")
)
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
    projects = [path.resolve() for path in args.projects] or [
        FIXTURE,
        *ARCHITECTURE_FIXTURES,
        REPOSITORY,
    ]
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
                failures += _compare_imports(
                    copy, label=f"{project.name} {variant}", native=native
                )
                for limit in LIMITS:
                    label = f"{project.name} {variant} limit={limit}"
                    failures += _compare(copy, limit, label=label, native=native)
                if project in ARCHITECTURE_FIXTURES:
                    for graph in GRAPH_POLICIES:
                        label = (
                            f"{project.name} transitive={graph['transitive']} "
                            f"type_checking={graph['type_checking']} "
                            f"cycles={graph['type_checking_cycles']}"
                        )
                        failures += _compare(
                            copy, None, label=label, native=native, graph=graph
                        )
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


def _compare(
    root: Path,
    limit: int | None,
    *,
    label: str,
    native: Path,
    graph: dict[str, Any] | None = None,
) -> int:
    config_path = root / "smelt.yml"
    original = config_path.read_text(encoding="utf-8")
    if limit is not None or graph is not None:
        raw = yaml.safe_load(original)
        if limit is not None:
            rules = raw.setdefault("rules", {})
            for code, (name, minimum) in LIMIT_RULES.items():
                rules.pop(code, None)
                rules[name] = {"severity": "warning", "max": max(limit, minimum)}
            # MOD008 only runs under an error policy, which real projects may not set.
            raw.setdefault("style", {}).setdefault("errors", {"base": "project.Error"})
        if graph is not None:
            raw.setdefault("architecture", {}).setdefault("import_graph", {}).update(
                graph
            )
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


def _compare_imports(root: Path, *, label: str, native: Path) -> int:
    """Compare every resolved import and every shortest import chain."""
    config_path = root / "smelt.yml"
    loaded = load_config(config_path)
    context = AnalysisContext(root, loaded.config)
    index = context.imports
    roots = tuple(loaded.config.project.root_packages)
    expected: dict[str, Any] = {
        "details": [_detail(item) for item in index.all_imports()],
        "tests": [
            [path, _detail(item)]
            for path, item in index.imports_in_tests(context.syntax)
        ],
        "chains": {
            f"{module}|{str(include).lower()}|{label_}": _chain_digest(
                index.shortest_chains_from(
                    module, include_type_checking=include, first_party_roots=scope
                )
            )
            for module in index.modules()
            for include in (True, False)
            for label_, scope in (("all", None), ("first", roots[:1]))
        },
    }
    result = subprocess.run(  # noqa: S603 - the locally built client
        [str(native), "--internal-imports", str(config_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    actual = json.loads(result.stdout)
    differences = [
        f"{section}: {left} != {right}"
        for section in ("details", "tests")
        for left, right in zip(expected[section], actual[section], strict=False)
        if left != right
    ]
    differences.extend(
        f"{section}: {len(expected[section])} in python, {len(actual[section])} in rust"
        for section in ("details", "tests")
        if len(expected[section]) != len(actual[section])
    )
    differences.extend(
        f"chains {key}: {expected['chains'].get(key)} != {actual['chains'].get(key)}"
        for key in expected["chains"].keys() | actual["chains"].keys()
        if expected["chains"].get(key) != actual["chains"].get(key)
    )
    _emit(
        f"{label} imports: {len(expected['details'])} + {len(expected['tests'])} "
        f"imports, {len(expected['chains'])} chain sets, "
        f"{'match' if not differences else f'{len(differences)} DIFFER'}"
    )
    for difference in differences[:5]:
        _emit(f"  {difference}")
    return 1 if differences else 0


def _detail(detail: ImportDetail) -> dict[str, Any]:
    fields = asdict(detail)
    fields["names"] = list(detail.names)
    # Grimp keeps a byte-order mark in the text of the first line.
    fields["line_contents"] = detail.line_contents.removeprefix("﻿")
    return fields


def _chain_digest(paths: Iterable[tuple[ImportDetail, ...]]) -> list[int | str]:
    lines = [
        "|".join(
            f"{hop.importer}>{hop.imported}:{hop.line}:{hop.column}" for hop in path
        )
        for path in paths
    ]
    return [len(lines), hashlib.sha256("\n".join(lines).encode()).hexdigest()]


def _native_findings(
    config_path: Path, native: Path
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    result = subprocess.run(  # noqa: S603 - the locally built client
        [str(native), "--internal-core", str(config_path), ",".join(PORTED)],
        capture_output=True,
        # The native core reads installed packages from this environment, as Python does.
        env={**os.environ, "VIRTUAL_ENV": sys.prefix},
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
    left = _by_id([_normalized(finding) for finding in expected])
    right = _by_id([_normalized(finding) for finding in actual])
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


def _normalized(finding: dict[str, Any]) -> dict[str, Any]:
    """Drop the git ref of upgrade guide links: a checkout links `main`, a release its tag."""
    hint = finding.get("hint")
    if not isinstance(hint, str):
        return finding
    return {**finding, "hint": _GUIDE_REF.sub("/blob/REF/docs/upgrading", hint)}


_GUIDE_REF = re.compile(r"/blob/[^/]+/docs/upgrading")


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
