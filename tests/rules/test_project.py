from pathlib import Path

import pytest

from smelt.analysis.context import AnalysisContext
from smelt.config import calls, parse_config
from smelt.config.project_rules import Rule
from smelt.diagnostics.violation import Category, Severity, docs_url
from smelt.model import ModuleInfo, ModuleKind
from smelt.rules.base import GuideTarget
from smelt.rules.project import ProjectRule

_EVERYWHERE = calls.to("datetime.datetime.now")


def _rule(match: calls.CallMatcher = _EVERYWHERE) -> ProjectRule:
    return ProjectRule(
        Rule(
            name="acme/domain-clock",
            match=match,
            message="Direct clock read",
            guide="Use acme.clock.now().",
        )
    )


def _context(tmp_path: Path) -> AnalysisContext:
    config, _ = parse_config({"version": 2, "project": {"root_packages": ["app"]}})
    return AnalysisContext(tmp_path, config)


def _target(layer: str | None) -> GuideTarget:
    module = "app.domain.service"
    return GuideTarget(module, info=ModuleInfo(module, ModuleKind.FEATURE, layer=layer))


def test_catalog_entry_is_identified_by_the_qualified_name() -> None:
    rule = _rule()

    assert (rule.code, rule.name) == ("acme/domain-clock", "acme/domain-clock")
    assert (rule.category, rule.default_severity) == (
        Category.PROJECT,
        Severity.WARNING,
    )


def test_explains_the_finding_and_the_guide_without_filler() -> None:
    doc = _rule().explain()

    assert (doc.summary, doc.fix, doc.canonical) == (
        "Direct clock read",
        "Use acme.clock.now().",
        "Use acme.clock.now().",
    )
    assert (doc.rationale, doc.bad, doc.good) == ("", "datetime.datetime.now()", "")


def test_documentation_points_to_the_project_rules_page() -> None:
    rule = _rule()

    assert docs_url(rule.code, rule.category).endswith("/docs/project-rules.md")


@pytest.mark.parametrize(
    ("match", "layer", "guide"),
    [
        (_EVERYWHERE, "infra", "Use acme.clock.now()."),
        (_EVERYWHERE, None, "Use acme.clock.now()."),
        (_EVERYWHERE.in_layer("domain"), "domain", "Use acme.clock.now()."),
        (_EVERYWHERE.in_layer("domain"), "infra", None),
        (_EVERYWHERE.in_layer("domain"), None, None),
    ],
)
def test_guides_only_modules_the_rule_applies_to(
    tmp_path: Path, match: calls.CallMatcher, layer: str | None, guide: str | None
) -> None:
    assert _rule(match).guide(_context(tmp_path), _target(layer)) == guide
