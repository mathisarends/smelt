"""Whole-analysis reuse around rule execution, separate from per-file facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from smelt.analysis.workspace import workspace_members
from smelt.diagnostics.violation import Violation  # noqa: TC001 - runtime cache schema

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.violation import Severity
    from smelt.rules.base import Rule


@dataclass
class CachedAnalysis:
    violations: list[Violation]
    suppressed: int
    coverage: dict[str, Any]
    modules: int
    paths: list[str]


ANALYSIS = TypeAdapter(CachedAnalysis)


def analysis_inputs(
    ctx: AnalysisContext,
    active: list[tuple[Rule, Severity]],
    selection: tuple[str, ...],
) -> object:
    """Inventory and contents, including facts that can affect unchanged modules."""
    return [
        ctx.config.model_dump(mode="json"),
        [(rule.code, severity.value) for rule, severity in active],
        selection,
        [
            [
                source.module,
                source.path,
                source.is_package,
                ctx.files.content_id(source.path),
                ctx.python_targets.for_path(source.absolute).to_json(),
            ]
            for source in sorted(
                ctx.files.sources.values(), key=lambda source: source.path
            )
        ],
        [
            [path, test.test_root, ctx.files.content_id(path)]
            for path, test in sorted(ctx.files.tests.items())
        ],
        [
            [name, [(part.path, part.source_root, part.has_init) for part in parts]]
            for name, parts in sorted(ctx.files.package_parts.items())
        ],
        [entry.to_json() for entry in ctx.coverage.entries],
        ctx.coverage.summary(),
        [member.to_json() for member in workspace_members(ctx.root, store=ctx.store)],
    ]
