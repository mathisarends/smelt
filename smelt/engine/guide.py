"""The per-target writing guide: one instruction per active rule, from smelt.yml."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from smelt.diagnostics.violation import Severity
from smelt.engine.briefing import Target, governing_view, resolve_target, target_info
from smelt.engine.rule_settings import RuleSettings
from smelt.rules.base import GuideTarget
from smelt.rules.registry import build_rule_set
from smelt.rules.testing.location import mirror_path

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.config.loader import LoadedConfig
    from smelt.rules.base import RuleContract

_WIDTH = 88
_SECTIONS = (
    (Severity.ERROR, "Required"),
    (Severity.WARNING, "Expected"),
    (Severity.HINT, "Review (hints; check whether they apply)"),
)


@dataclass(frozen=True, slots=True)
class GuideEntry:
    code: str
    name: str
    family: str
    severity: Severity
    instruction: str
    tests: bool = False
    """Applies to the target's tests rather than its source."""

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "rule": self.name,
            "family": self.family,
            "severity": self.severity.value,
            "applies_to": "tests" if self.tests else "source",
            "instruction": self.instruction,
        }


@dataclass(frozen=True, slots=True)
class Guide:
    target: Target
    layer: str | None
    test_path: str | None
    entries: tuple[GuideEntry, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "target": {
                "kind": self.target.kind,
                "feature": self.target.feature,
                "module": self.target.module,
                "path": self.target.path,
                "planned": self.target.planned,
            },
            "layer": self.layer,
            "test_path": self.test_path,
            "rules": [entry.to_json() for entry in self.entries],
        }


def resolve_guide_target(ctx: AnalysisContext, raw: str | None) -> Target:
    """Like ``resolve_target``, but also accept existing and planned test files."""
    path = (raw or "").strip("/")
    if path.endswith(".py") and ctx.files.is_test_path(path):
        return Target("test", path=path, planned=not ctx.store.exists(ctx.root / path))
    return resolve_target(ctx, raw)


def build_guide(ctx: AnalysisContext, target: Target, loaded: LoadedConfig) -> Guide:
    """Collect the instruction of every rule active for ``target`` and its tests."""
    ctx, _ = governing_view(ctx, target)
    info = target_info(ctx.model, target)
    rules = build_rule_set()
    settings = RuleSettings(rules, loaded)
    test_path = target.path if target.kind == "test" else _mirrored_test(ctx, target)
    entries: list[GuideEntry] = []
    if target.kind != "test":
        module = target.module or _feature_package(ctx, target)
        # Project-scoped rules govern configuration and files, not one module.
        excluded = {"test"} if target.kind == "project" else {"test", "project"}
        entries.extend(
            _entries(
                ctx,
                settings,
                rules=[rule for rule in rules.rules if _scope(rule) not in excluded],
                target=GuideTarget(module or "", target.path, info),
            )
        )
    entries.extend(
        _entries(
            ctx,
            settings,
            rules=[rule for rule in rules.rules if _scope(rule) == "test"],
            target=GuideTarget("", test_path),
            tests=True,
        )
    )
    return Guide(target, info.layer if info else None, test_path, tuple(entries))


def render_guide(guide: Guide) -> str:
    lines = [_heading(guide)]
    if guide.layer:
        lines.append(f"Layer: {guide.layer}")
    source = [entry for entry in guide.entries if not entry.tests]
    tests = [entry for entry in guide.entries if entry.tests]
    lines.extend(_section_lines(source))
    if tests and guide.target.kind != "test":
        title = f"Tests ({guide.test_path})" if guide.test_path else "Tests"
        lines.extend(("", title))
    lines.extend(_section_lines(tests))
    if not guide.entries:
        lines.extend(("", "No rules are active for this target."))
    return "\n".join(lines) + "\n"


def _entries(
    ctx: AnalysisContext,
    settings: RuleSettings,
    *,
    rules: list[RuleContract],
    target: GuideTarget,
    tests: bool = False,
) -> list[GuideEntry]:
    found: list[GuideEntry] = []
    for rule in rules:
        decision = settings.decision(rule, ctx, target.module or None, path=target.path)
        if decision.severity is None:
            continue
        instruction = rule.guide(ctx, target)
        if instruction is None:
            continue
        found.append(
            GuideEntry(
                code=rule.code,
                name=rule.name,
                family=rule.category.value,
                severity=decision.severity,
                instruction=instruction,
                tests=tests,
            )
        )
    return found


def _section_lines(entries: list[GuideEntry]) -> list[str]:
    lines: list[str] = []
    for severity, title in _SECTIONS:
        matching = sorted(
            (entry for entry in entries if entry.severity is severity),
            key=lambda entry: entry.code,
        )
        if not matching:
            continue
        lines.extend(("", f"{title}:"))
        for entry in matching:
            lines.extend(
                textwrap.wrap(
                    entry.instruction,
                    _WIDTH,
                    initial_indent=f"  {entry.code}  ",
                    subsequent_indent=" " * 10,
                    break_on_hyphens=False,
                )
            )
    return lines


def _heading(guide: Guide) -> str:
    target = guide.target
    planned = " [planned]" if target.planned else ""
    match target.kind:
        case "project":
            return "Guide: project"
        case "feature":
            return f"Guide: feature {target.feature}"
        case "test":
            return f"Guide: test {target.path}{planned}"
        case _:
            return f"Guide: {target.module} ({target.path}){planned}"


def _mirrored_test(ctx: AnalysisContext, target: Target) -> str | None:
    if ctx.config.tests.layout != "mirror" or target.kind != "module":
        return None
    if target.module is None or target.path is None:
        return None
    if PurePosixPath(target.path).name == "__init__.py":
        return None
    return mirror_path(ctx, target.module)


def _feature_package(ctx: AnalysisContext, target: Target) -> str | None:
    if target.feature is None:
        return None
    return ctx.model.feature_package_for(target.feature)


def _scope(rule: RuleContract) -> str:
    return getattr(rule, "scope", "source")
