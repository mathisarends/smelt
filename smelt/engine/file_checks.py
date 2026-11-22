"""Reuse explicitly file-local rule decisions without narrowing global analysis."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from pydantic import TypeAdapter

from smelt.analysis.identity import IdentityScope, analyzer_identity
from smelt.config.workspace import scope_for_path
from smelt.diagnostics.violation import Violation
from smelt.rules.identities import implementation_identity

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.files import SourceFile
    from smelt.rules.base import RuleContract


class _RuleDecision(TypedDict):
    """One rule's findings for a source, valid for one implementation."""

    identity: str
    findings: list[Violation]


@dataclass(frozen=True, slots=True)
class _SourceDecisions:
    """One source's reusable decisions and the rules that must decide again."""

    source: SourceFile
    exact: str
    latest: str
    decisions: dict[str, _RuleDecision]
    stale: list[RuleContract]


_VIOLATIONS = TypeAdapter(list[Violation])
_FILE_DECISIONS = TypeAdapter(dict[str, _RuleDecision])


def rule_findings(rule: RuleContract, ctx: AnalysisContext) -> Iterator[Violation]:
    if getattr(rule, "architecture_scoped", False) and ctx.model.parts:
        yield from _architecture_findings(rule, ctx)
        return
    cache = ctx.cache
    if cache is not None and getattr(rule, "production_only", False):
        key = cache.decision_key(
            "production-check",
            [rule.code, ctx.production_key],
            identity=implementation_identity(rule, cache),
        )
        findings = cache.read(key, _VIOLATIONS)
        if findings is None:
            findings = list(rule.check(ctx))
            cache.write(key, findings, _VIOLATIONS)
        yield from findings
        return
    if cache is not None and getattr(rule, "member_local", False):
        yield from _member_findings(rule, ctx)
        return
    if cache is not None and getattr(rule, "test_local", False):
        yield from _test_findings(rule, ctx)
        return
    yield from rule.check(ctx)


def file_findings(
    rules: list[RuleContract], ctx: AnalysisContext
) -> dict[str, list[Violation]]:
    """Store each source's decisions of the selected file-local rules.

    One entry holds the decisions of one state of all those rules, so another
    checkout or a reverted edit finds its own entry again. When that entry is
    missing, the source's latest entry supplies every decision whose rule
    implementation is unchanged, and only the edited rules decide again.
    """
    cache = ctx.cache
    if cache is None or not rules:
        return {}
    by_rule: dict[str, list[Violation]] = {rule.code: [] for rule in rules}
    identities = {rule.code: implementation_identity(rule, cache) for rule in rules}
    policy = _digest([list(by_rule), ctx.config.model_dump(mode="json")])
    host = analyzer_identity(IdentityScope.RULE_HOST)
    state = _digest([host, sorted(identities.items())])
    entries: list[_SourceDecisions] = []
    for source in ctx.files.sources.values():
        inputs = [
            policy,
            source.module,
            source.path,
            source.is_package,
            ctx.files.content_id(source.path),
            ctx.python_targets.for_path(source.absolute).to_json(),
        ]
        exact = cache.decision_key("file-decisions", inputs, identity=state)
        latest = cache.decision_key("latest-file-decisions", inputs, identity=host)
        decisions = cache.read(exact, _FILE_DECISIONS)
        stale: list[RuleContract] = []
        if decisions is None:
            decisions = cache.read(latest, _FILE_DECISIONS) or {}
            stale = [
                rule
                for rule in rules
                if rule.code not in decisions
                or decisions[rule.code]["identity"] != identities[rule.code]
            ]
            # The reused decisions become this state's entry even if none is stale.
            if not stale:
                cache.write(exact, decisions, _FILE_DECISIONS)
        entries.append(_SourceDecisions(source, exact, latest, decisions, stale))
    ctx.store.prefetch(entry.source.absolute for entry in entries if entry.stale)
    for entry in entries:
        decisions = entry.decisions
        if entry.stale:
            view = ctx.file_view(entry.source)
            for rule in entry.stale:
                decisions[rule.code] = {
                    "identity": identities[rule.code],
                    "findings": list(rule.check(view)),
                }
            decisions = {code: decisions[code] for code in by_rule}
            for key in (entry.exact, entry.latest):
                cache.write(key, decisions, _FILE_DECISIONS)
        for rule in rules:
            for finding in decisions[rule.code]["findings"]:
                by_rule[finding.code].append(finding)
    return by_rule


def _architecture_findings(
    rule: RuleContract, ctx: AnalysisContext
) -> Iterator[Violation]:
    """Each architecture judges the files of its own packages and members."""
    scopes = ctx.architecture_scopes
    for scope in scopes:
        for finding in rule_findings(rule, ctx.architecture_view(scope)):
            if scope_for_path(scopes, finding.path) is scope:
                yield finding


def _test_findings(rule: RuleContract, ctx: AnalysisContext) -> Iterator[Violation]:
    cache = ctx.cache
    if cache is None:
        yield from rule.check(ctx)
        return
    policy = _digest(
        [
            ctx.config.model_dump(mode="json"),
            sorted(ctx.files.sources),
            sorted(ctx.files.packages),
        ]
    )
    identity = implementation_identity(rule, cache)
    for path in ctx.test_syntax.modules:
        key = cache.decision_key(
            "test-check",
            [rule.code, policy, ctx.test_syntax.inputs(path)],
            identity=identity,
        )
        findings = cache.read(key, _VIOLATIONS)
        if findings is None:
            findings = list(rule.check(ctx.test_view(path)))
            cache.write(key, findings, _VIOLATIONS)
        yield from findings


def _member_findings(rule: RuleContract, ctx: AnalysisContext) -> Iterator[Violation]:
    cache = ctx.cache
    if cache is None:
        yield from rule.check(ctx)
        return
    policy = _digest(ctx.config.model_dump(mode="json"))
    identity = implementation_identity(rule, cache)
    index = ctx.member_usage
    for path in index.local_paths():
        key = cache.decision_key(
            "member-check",
            [rule.code, policy, index.decision_key(path)],
            identity=identity,
        )
        findings = cache.read(key, _VIOLATIONS)
        if findings is None:
            findings = list(rule.check(ctx.member_view(path)))
            cache.write(key, findings, _VIOLATIONS)
        yield from findings


def _digest(inputs: object) -> str:
    """Policy inputs shared by many keys; the identity is added once per key."""
    payload = json.dumps(inputs, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()
