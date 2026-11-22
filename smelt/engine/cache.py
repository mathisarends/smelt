"""Whole-analysis reuse around rule execution, separate from per-file facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from smelt.analysis.installed_exports import InstalledPackages
from smelt.analysis.workspace import workspace_members
from smelt.diagnostics.violation import Violation  # noqa: TC001 - runtime cache schema

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.config.models import SmeltConfig
    from smelt.diagnostics.violation import Severity
    from smelt.rules.base import RuleContract


@dataclass(slots=True)
class CachedAnalysis:
    violations: list[Violation]
    suppressed: int
    coverage: dict[str, Any]
    modules: int
    paths: list[str]
    dependencies: dict[str, str] = field(default_factory=dict)


ANALYSIS = TypeAdapter(CachedAnalysis)


def analysis_inputs(
    ctx: AnalysisContext,
    active: list[tuple[RuleContract, Severity]],
    selection: tuple[str, ...],
    *,
    installed: dict[str, str | None],
) -> object:
    """Inventory and contents, including facts that can affect unchanged modules."""
    return [
        ctx.config.model_dump(mode="json"),
        [(rule.code, severity.value) for rule, severity in active],
        selection,
        ctx.files.yaml_paths,
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
        ctx.workspace.metadata
        if any(rule.code == "ARC010" for rule, _ in active)
        else None,
        installed,
    ]


def installed_inputs(
    config: SmeltConfig, active: list[tuple[RuleContract, Severity]]
) -> dict[str, str | None]:
    """Installed third-party sources that IMP004 reads outside the project."""
    if not any(rule.code == "IMP004" for rule, _ in active):
        return {}
    packages = {
        name
        for policy in (config.structure, *config.structure.contracts.values())
        for name in policy.third_party_reexports or ()
    }
    installed = InstalledPackages()
    return installed.identity(packages)
