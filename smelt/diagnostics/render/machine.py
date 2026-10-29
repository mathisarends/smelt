from __future__ import annotations

import json
import posixpath
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from smelt.diagnostics.groups import group_findings
from smelt.diagnostics.violation import Severity, Violation

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smelt.diagnostics.report import Report

JSON_SCHEMA_VERSION = 1
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
TOOL_URI = "https://github.com/mathisarends/smelt"


def render_json(report: Report) -> str:
    document = {
        "schema_version": JSON_SCHEMA_VERSION,
        "status": report.status,
        "summary": report.summary_json(),
        "scope": {
            "mode": "changed" if report.comparison else "full",
            "paths": list(report.scope),
            "files": report.checked_files,
            "rules": [rule.code for rule in report.rules],
        },
        "comparison": dict(report.comparison) if report.comparison else None,
        "coverage": dict(report.coverage) if report.coverage else None,
        "project": dict(report.project) if report.project else None,
        "violations": [v.to_json() for v in report.violations],
        "configuration_diagnostics": [
            diagnostic.to_json() for diagnostic in report.configuration_diagnostics
        ],
        "groups": group_findings(report.violations),
    }
    if report.fixes is not None:
        document["fixes"] = dict(report.fixes)
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def render_error_json(
    kind: str,
    message: str,
    *,
    invalid_input: Mapping[str, str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> str:
    """The document ``--format json`` prints instead of a report when it exits with 2."""
    document = {
        "schema_version": JSON_SCHEMA_VERSION,
        "status": "error",
        "error": {
            "kind": kind,
            "message": message,
            "input": dict(invalid_input) if invalid_input is not None else None,
            "config": dict(config) if config is not None else None,
        },
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
    prefix = (report.project or {}).get("repository_prefix", "")
    results = [_sarif_result(v, index, prefix) for v in report.violations]
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
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "toolConfigurationNotifications": [
                            {
                                "level": _SARIF_LEVEL[diagnostic.severity],
                                "message": {
                                    "text": f"{diagnostic.key}: {diagnostic.message}"
                                },
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {
                                                "uri": quote(
                                                    posixpath.join(
                                                        prefix, diagnostic.path
                                                    ),
                                                    safe="/",
                                                )
                                            }
                                        }
                                    }
                                ],
                            }
                            for diagnostic in report.configuration_diagnostics
                        ],
                    }
                ],
                "properties": _sarif_properties(report),
            }
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _sarif_properties(report: Report) -> dict[str, Any]:
    """Provenance a SARIF consumer can keep with the results."""
    coverage = report.coverage or {}
    return {
        "mode": "changed" if report.comparison else "full",
        "comparison": dict(report.comparison) if report.comparison else None,
        "policyHash": coverage.get("policy_hash"),
        **({"fixes": dict(report.fixes)} if report.fixes is not None else {}),
        "project": dict(report.project) if report.project else None,
    }


def _sarif_result(
    violation: Violation, index: dict[str, int], prefix: str = ""
) -> dict[str, Any]:
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
        location: dict[str, Any] = {
            "artifactLocation": {
                "uri": quote(posixpath.join(prefix, violation.path), safe="/")
            }
        }
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
    prefix = (report.project or {}).get("repository_prefix", "")
    lines = [_github_line(v, prefix) for v in report.violations]
    lines.extend(
        f"::{_GITHUB_LEVEL[diagnostic.severity]} file={_escape_property(posixpath.join(prefix, diagnostic.path))},title=Smelt configuration::{_escape_data(f'{diagnostic.key}: {diagnostic.message}')}"
        for diagnostic in report.configuration_diagnostics
    )
    summary = report.summary_json()
    lines.append(
        f"smelt: {summary['errors']} errors, {summary['warnings']} warnings, "
        f"{summary['hints']} hints, {summary['modules']} modules"
    )
    return "\n".join(lines) + "\n"


def _github_line(violation: Violation, prefix: str = "") -> str:
    properties: list[str] = []
    if violation.path:
        properties.append(
            f"file={_escape_property(posixpath.join(prefix, violation.path))}"
        )
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
