from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.debt import Debt
from smelt.diagnostics.groups import group_findings
from smelt.diagnostics.render.machine import render_json
from smelt.diagnostics.render.text import render_concise, render_text
from smelt.diagnostics.violation import ImportLink, Severity, Violation
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

SHARED_WAIT = """import subprocess
def run_import():
    return subprocess.run(['python'], check=False)
def test_first():
    run_import()
def test_second():
    run_import()
"""


@pytest.mark.parametrize("renderer", [render_concise, render_text])
def test_shared_helper_has_one_repair_and_preserves_each_finding(
    tmp_path: Path, renderer: object
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "tests/test_imports.py": SHARED_WAIT,
        },
    )
    report = check(tmp_path, CheckOptions(select=("SMT426",))).report
    document = json.loads(render_json(report))
    [group] = document["groups"]
    assert len(document["violations"]) == group["count"] == 2
    assert group["kind"] == "shared_helper"
    assert group["location"] == {
        "path": "tests/test_imports.py",
        "line": 3,
        "column": 12,
    }
    assert group["helper"] == {"name": "run_import", "definition_line": 2}
    assert [test["call_line"] for test in group["affected_tests"]] == [5, 7]
    assert sorted(group["violations"]) == sorted(
        finding.finding_id for finding in report.violations
    )
    assert group_findings(list(reversed(report.violations))) == document["groups"]
    text = (
        render_concise(report)
        if renderer is render_concise
        else render_text(report, lambda _path, _line: "")
    )
    assert text.count("Bound this operation") == 1
    assert "test_first" in text
    assert "test_second" in text
    assert report.summary_json()["warnings"] == 2


def test_shared_helper_groups_separate_operations_and_respect_suppression(
    tmp_path: Path,
) -> None:
    source = SHARED_WAIT.replace(
        "return subprocess.run(['python'], check=False)",
        "subprocess.run(['python'], check=False); subprocess.run(['other'], check=False)",
    )
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "tests/test_imports.py": source,
        },
    )
    report = check(tmp_path, CheckOptions(select=("SMT426",))).report
    groups = group_findings(report.violations)
    assert len(groups) == 2
    assert groups[0]["location"]["column"] != groups[1]["location"]["column"]
    assert render_concise(report).count("Bound this operation") == 2
    write_project(
        tmp_path,
        {
            "tests/test_imports.py": SHARED_WAIT.replace(
                "check=False)",
                "check=False)  # smelt: ignore[SMT426] -- parent enforces a deadline",
            )
        },
    )
    suppressed = check(tmp_path, CheckOptions(select=("SMT426",))).report
    assert suppressed.suppressed == 2
    assert group_findings(suppressed.violations) == []


def test_grouping_never_reintroduces_a_hidden_hint_or_filtered_finding(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "tests/test_imports.py": SHARED_WAIT,
        },
    )
    report = check(tmp_path, CheckOptions(select=("SMT426",))).report
    first, second = report.violations
    report.violations = [first, replace(second, severity=Severity.HINT)]
    assert group_findings(report.violations) == []
    assert "test_second" not in render_concise(report)
    report.violations = [first]
    assert group_findings(report.violations) == []


def test_group_metadata_preserves_existing_finding_and_debt_identities(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "tests/test_imports.py": SHARED_WAIT,
        },
    )
    current = check(tmp_path, CheckOptions(select=("SMT426",))).report.violations
    previous = []
    for finding in current:
        expected = dict(finding.expected or {})
        expected.pop("operation_location")
        expected["helper"] = {
            key: value
            for key, value in expected["helper"].items()
            if key not in {"definition_line", "call_lines"}
        }
        previous.append(replace(finding, expected=expected))
    assert [finding.finding_id for finding in previous] == [
        finding.finding_id for finding in current
    ]
    assert (
        Debt.from_violations(
            previous, lambda _finding: "subprocess.run(['python'], check=False)"
        ).entries
        == Debt.from_violations(
            current, lambda _finding: "subprocess.run(['python'], check=False)"
        ).entries
    )


def _import(
    path: str,
    edge: str,
    chain: tuple[ImportLink, ...] = (),
    code: str = "SMT101",
) -> Violation:
    return Violation(
        code=code,
        rule="layer-boundary",
        severity=Severity.ERROR,
        message=f"{edge} in {path}",
        path=path,
        line=1,
        source_module=path.removesuffix(".py").replace("/", "."),
        target_module="app.llm.providers.openai",
        import_chain=chain,
        edge=edge,
    )


def _through_facade(importer: str) -> tuple[ImportLink, ...]:
    return (
        ImportLink(importer, "app.llm", 1),
        ImportLink("app.llm", "app.llm.providers.openai", 3),
    )


class TestGroupFindings:
    def test_facade_groups_every_finding_it_relays(self) -> None:
        found = [
            _import(
                "app/chat/a.py",
                "chat.application -> infrastructure",
                _through_facade("app.chat.a"),
            ),
            _import(
                "app/chat/b.py",
                "chat.application -> infrastructure",
                _through_facade("app.chat.b"),
            ),
            _import(
                "app/user/c.py",
                "user.domain -> infrastructure",
                _through_facade("app.user.c"),
                code="SMT102",
            ),
            _import("app/user/d.py", "user.domain -> infrastructure"),
        ]

        groups = group_findings(found)

        facade = next(g for g in groups if g["kind"] == "facade")
        assert facade == {
            "kind": "facade",
            "key": "app.llm",
            "count": 3,
            "codes": ["SMT101", "SMT102"],
            "violations": [v.finding_id for v in found[:3]],
            "direct": 0,
            "transitive": 3,
            "edges": [
                "chat.application -> infrastructure",
                "user.domain -> infrastructure",
            ],
        }
        edges = {g["key"]: g for g in groups if g["kind"] == "edge"}
        assert edges["SMT101 chat.application -> infrastructure"]["count"] == 2
        mixed = edges.get("SMT101 user.domain -> infrastructure")
        assert mixed is None  # one SMT101 and one SMT102: two decisions

    def test_edge_groups_count_direct_and_transitive(self) -> None:
        found = [
            _import("app/a.py", "a -> b"),
            _import("app/c.py", "a -> b", _through_facade("app.c")),
        ]

        [edge] = [g for g in group_findings(found) if g["kind"] == "edge"]

        assert (edge["direct"], edge["transitive"]) == (1, 1)

    def test_cycle_keeps_its_witness_path(self) -> None:
        chain = (
            ImportLink("app.features.x", "app.platform.auth", 6),
            ImportLink("app.platform.auth.guard", "app.features.y", 8),
        )
        cycle = Violation(
            code="SMT104",
            rule="import-cycle",
            severity=Severity.ERROR,
            message="import cycle",
            path="app/features/x.py",
            import_chain=chain,
            expected={"cycle": ["features", "platform"], "type_checking_only": True},
            edge="features -> platform -> features",
        )

        [group] = group_findings([cycle])

        assert group["kind"] == "cycle"
        assert group["witness"] == [link.to_json() for link in chain]
        assert group["type_checking_only"] is True

    def test_test_moves_group_by_directories(self) -> None:
        def move(name: str) -> Violation:
            return Violation(
                code="SMT401",
                rule="test-location",
                severity=Severity.ERROR,
                message=f"{name} belongs in tests/auth/infrastructure/",
                path=f"tests/auth/{name}",
                expected={"path": f"tests/auth/infrastructure/{name}"},
            )

        [group] = group_findings([move("test_a.py"), move("test_b.py")])

        assert group["key"] == "tests/auth/ -> tests/auth/infrastructure/"
        assert group["count"] == 2

    def test_order_is_deterministic(self) -> None:
        found = [_import(f"app/m{n}.py", f"e{n % 2} -> x") for n in range(4)]

        keys = [(g["kind"], g["key"]) for g in group_findings(found)]
        reversed_keys = [
            (g["kind"], g["key"]) for g in group_findings(list(reversed(found)))
        ]

        assert (
            keys
            == reversed_keys
            == [("edge", "SMT101 e0 -> x"), ("edge", "SMT101 e1 -> x")]
        )
