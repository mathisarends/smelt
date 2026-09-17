from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from smelt.diagnostics.violation import Severity, Violation

if TYPE_CHECKING:
    from smelt.diagnostics.report import Report

JSON_SCHEMA_VERSION = 1
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
TOOL_URI = "https://github.com/mathisarends/smelt"


def render_json(report: Report) -> str:
    document = {
        "schema_version": JSON_SCHEMA_VERSION,
        "status": report.status,
        "summary": report.summary_json(),
        "violations": [v.to_json() for v in report.violations],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


_SARIF_LEVEL = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.HINT: "note",
}


def render_sarif(report: Report, version: str) -> str:
    rules = [
        {
            "id": meta.code,
            "name": meta.name,
            "shortDescription": {"text": meta.summary},
            "helpUri": meta.docs_url,
            "defaultConfiguration": {"level": _SARIF_LEVEL[meta.default_severity]},
            "properties": {"category": meta.category.value},
        }
        for meta in report.rules
    ]
    index = {meta.code: i for i, meta in enumerate(report.rules)}
    results = [_sarif_result(v, index) for v in report.violations]
    document = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "smelt",
                        "version": version,
                        "informationUri": TOOL_URI,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _sarif_result(violation: Violation, index: dict[str, int]) -> dict[str, Any]:
    text = violation.message
    if violation.hint:
        text += f"\n{violation.hint}"
    result: dict[str, Any] = {
        "ruleId": violation.code,
        "level": _SARIF_LEVEL[violation.severity],
        "message": {"text": text},
    }
    if violation.code in index:
        result["ruleIndex"] = index[violation.code]
    if violation.path:
        region: dict[str, int] = {}
        if violation.line:
            region["startLine"] = violation.line
            if violation.column:
                region["startColumn"] = violation.column
            if violation.end_line:
                region["endLine"] = violation.end_line
            if violation.end_column:
                region["endColumn"] = violation.end_column
        location: dict[str, Any] = {"artifactLocation": {"uri": violation.path}}
        if region:
            location["region"] = region
        result["locations"] = [{"physicalLocation": location}]
    return result


_GITHUB_LEVEL = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.HINT: "notice",
}


def render_github(report: Report) -> str:
    lines = [_github_line(v) for v in report.violations]
    summary = report.summary_json()
    lines.append(
        f"smelt: {summary['errors']} errors, {summary['warnings']} warnings, "
        f"{summary['hints']} hints, {summary['modules']} modules"
    )
    return "\n".join(lines) + "\n"


def _github_line(violation: Violation) -> str:
    properties: list[str] = []
    if violation.path:
        properties.append(f"file={_escape_property(violation.path)}")
    if violation.line:
        properties.append(f"line={violation.line}")
    if violation.column:
        properties.append(f"col={violation.column}")
    if violation.end_line:
        properties.append(f"endLine={violation.end_line}")
    if violation.end_column:
        properties.append(f"endColumn={violation.end_column}")
    properties.append(f"title={_escape_property(f'{violation.code} {violation.rule}')}")
    message = violation.message
    if violation.hint:
        message += f"\nHint: {violation.hint}"
    level = _GITHUB_LEVEL[violation.severity]
    return f"::{level} {','.join(properties)}::{_escape_data(message)}"


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")
