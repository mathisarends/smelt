from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from smelt.diagnostics.render.machine import render_github, render_json, render_sarif

if TYPE_CHECKING:
    from smelt.diagnostics.report import Report


def _loads(text: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(text)
    return document


class TestJson:
    def test_document_shape(self, report: Report) -> None:
        document = _loads(render_json(report))

        assert document["schema_version"] == 1
        assert document["status"] == "failed"
        assert document["summary"] == {
            "errors": 1,
            "warnings": 1,
            "hints": 1,
            "modules": 4,
            "suppressed": 1,
            "in_debt": 2,
        }
        assert [v["code"] for v in document["violations"]] == [
            "SMT101",
            "SMT303",
            "SMT305",
        ]

    def test_violation_fields(self, report: Report) -> None:
        first = _loads(render_json(report))["violations"][0]

        assert first == {
            "code": "SMT101",
            "rule": "layer-boundary",
            "severity": "error",
            "category": "dependencies",
            "message": "domain must not import infrastructure",
            "path": "gw/features/voice/domain/calls.py",
            "line": 1,
            "column": 1,
            "end_line": 1,
            "end_column": 40,
            "feature": "voice",
            "layer": "domain",
            "source_module": "gw.features.voice.domain.calls",
            "target_module": "gw.features.voice.infra.sql",
            "import_chain": [
                {
                    "importer": "gw.features.voice.domain.calls",
                    "imported": "gw.features.voice.infra.sql",
                    "line": 1,
                }
            ],
            "expected": {"may_depend_on": []},
            "hint": "Depend on a port instead.",
            "docs_url": "https://example.test/rules/SMT101",
        }

    def test_clean_report_passes(self, clean_report: Report) -> None:
        document = _loads(render_json(clean_report))

        assert document["status"] == "passed"
        assert document["violations"] == []


class TestSarif:
    def test_driver_lists_the_rules(self, report: Report) -> None:
        driver = _loads(render_sarif(report, "0.1.0"))["runs"][0]["tool"]["driver"]

        assert driver["name"] == "smelt"
        assert driver["version"] == "0.1.0"
        assert [rule["id"] for rule in driver["rules"]] == ["SMT101", "SMT303"]
        assert driver["rules"][1]["properties"] == {"category": "structure"}

    def test_result_carries_location_and_hint(self, report: Report) -> None:
        results = _loads(render_sarif(report, "0.1.0"))["runs"][0]["results"]

        assert results[0] == {
            "ruleId": "SMT101",
            "level": "error",
            "message": {
                "text": "domain must not import infrastructure\nDepend on a port instead."
            },
            "ruleIndex": 0,
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": "gw/features/voice/domain/calls.py"
                        },
                        "region": {
                            "startLine": 1,
                            "startColumn": 1,
                            "endLine": 1,
                            "endColumn": 40,
                        },
                    }
                }
            ],
        }

    def test_hint_severity_becomes_a_note(self, report: Report) -> None:
        results = _loads(render_sarif(report, "0.1.0"))["runs"][0]["results"]

        assert results[2]["level"] == "note"
        assert "ruleIndex" not in results[2]
        assert results[2]["locations"] == [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "gw/features/voice/infra/"}
                }
            }
        ]


class TestGithub:
    def test_annotations_and_summary(self, report: Report) -> None:
        lines = render_github(report).splitlines()

        assert lines[0] == (
            "::error file=gw/features/voice/domain/calls.py,line=1,col=1,"
            "endLine=1,endColumn=40,title=SMT101 layer-boundary::"
            "domain must not import infrastructure%0AHint: Depend on a port instead."
        )
        assert lines[2].startswith("::notice file=gw/features/voice/infra/,")
        assert lines[3] == "smelt: 1 errors, 1 warnings, 1 hints, 4 modules"

    def test_properties_are_escaped(self, report: Report) -> None:
        assert "title=SMT303 role-file::" in render_github(report)
