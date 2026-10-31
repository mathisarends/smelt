"""Reuse explicitly file-local rule decisions without narrowing global analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.diagnostics.violation import Violation

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.files import SourceFile
    from smelt.rules.base import Rule

_VIOLATIONS = TypeAdapter(list[Violation])


def rule_findings(rule: Rule, ctx: AnalysisContext) -> Iterator[Violation]:
    cache = ctx.cache
    if cache is not None and getattr(rule, "member_local", False):
        yield from _member_findings(rule, ctx)
        return
    if cache is not None and getattr(rule, "test_local", False):
        yield from _test_findings(rule, ctx)
        return
    if cache is None or not getattr(rule, "file_local", False):
        yield from rule.check(ctx)
        return
    policy = cache.key("file-policy", ctx.config.model_dump(mode="json"))
    entries: list[tuple[SourceFile, str, list[Violation] | None]] = []
    for source in ctx.files.sources.values():
        key = cache.key(
            "file-check",
            [
                rule.code,
                policy,
                source.module,
                source.path,
                source.is_package,
                ctx.files.content_id(source.path),
                ctx.python_targets.for_path(source.absolute).to_json(),
            ],
        )
        entries.append((source, key, cache.read(key, _VIOLATIONS)))
    ctx.store.prefetch(
        source.absolute for source, _, cached in entries if cached is None
    )
    for source, key, cached in entries:
        findings = cached
        if findings is None:
            findings = list(rule.check(ctx.file_view(source)))
            cache.write(key, findings, _VIOLATIONS)
        yield from findings


def _test_findings(rule: Rule, ctx: AnalysisContext) -> Iterator[Violation]:
    cache = ctx.cache
    if cache is None:
        yield from rule.check(ctx)
        return
    policy = cache.key(
        "test-policy",
        [
            ctx.config.model_dump(mode="json"),
            sorted(ctx.files.sources),
            sorted(ctx.files.packages),
        ],
    )
    for path in ctx.test_syntax.modules:
        key = cache.key("test-check", [rule.code, policy, ctx.test_syntax.inputs(path)])
        findings = cache.read(key, _VIOLATIONS)
        if findings is None:
            findings = list(rule.check(ctx.test_view(path)))
            cache.write(key, findings, _VIOLATIONS)
        yield from findings


def _member_findings(rule: Rule, ctx: AnalysisContext) -> Iterator[Violation]:
    cache = ctx.cache
    if cache is None:
        yield from rule.check(ctx)
        return
    policy = cache.key("member-policy", ctx.config.model_dump(mode="json"))
    index = ctx.member_usage
    for path in index.local_paths():
        key = cache.key("member-check", [rule.code, policy, index.decision_key(path)])
        findings = cache.read(key, _VIOLATIONS)
        if findings is None:
            findings = list(rule.check(ctx.member_view(path)))
            cache.write(key, findings, _VIOLATIONS)
        yield from findings
