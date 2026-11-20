import importlib
import inspect
import os
import pkgutil
import re
import subprocess
import sys
from copy import copy
from pathlib import Path

import pytest

import smelt.rules
from smelt.analysis.context import Index
from smelt.config.models import SmeltConfig
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleContract, RuleSet
from smelt.rules.registry import build_rule_set

# Code prefixes per family, as listed in CONTRIBUTING.md.
_CODE_PREFIXES = {
    Category.ARCHITECTURE: "ARC",
    Category.IMPORTS: "IMP",
    Category.LAYOUT: "LAY",
    Category.VISIBILITY: "VIS",
    Category.MODELING: "MOD",
    Category.TYPING: "TYP",
    Category.COMPLEXITY: "CPX",
    Category.IDIOMS: "IDM",
    Category.FRAMEWORKS: "FWK",
    Category.TESTS: "TST",
    Category.META: "MET",
}

# Qualifiers the naming scheme in CONTRIBUTING.md replaces with
# forbidden-, redundant- or a finding noun.
_RETIRED_QUALIFIERS = ("no-", "unnecessary-", "review-", "require-", "check-")


class TestBuiltins:
    def test_every_public_rule_implementation_is_registered(self) -> None:
        implementations = set()
        for info in pkgutil.walk_packages(smelt.rules.__path__, "smelt.rules."):
            module = importlib.import_module(info.name)
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if (
                    cls.__module__ == info.name
                    and not name.startswith("_")
                    and cls is not Rule
                    and issubclass(cls, Rule)
                ):
                    implementations.add(cls)

        registered = {type(rule) for rule in build_rule_set().rules}
        assert implementations == registered, (
            "Register new rules in smelt/rules/registry.py; keep abstract helpers private",
            implementations ^ registered,
        )

    def test_every_rule_meets_the_catalog_contract(self) -> None:
        problems = [
            f"{rule.code}: {problem}"
            for rule in build_rule_set().rules
            for problem in _catalog_problems(rule)
        ]

        assert problems == []

    def test_codes_are_unique(self) -> None:
        codes = [rule.code for rule in build_rule_set().rules]

        assert len(codes) == len(set(codes))

    def test_rules_are_sorted_by_code(self) -> None:
        rules = build_rule_set().rules

        assert [rule.code for rule in rules] == sorted(rule.code for rule in rules)

    def test_lookup_by_code_and_by_name(self) -> None:
        rule_set = build_rule_set()

        assert rule_set.lookup("arc001") is rule_set.by_code("ARC001")
        assert rule_set.lookup("architecture/layer-boundary") is rule_set.by_code(
            "ARC001"
        )
        assert rule_set.lookup("nope") is None


@pytest.mark.parametrize("attribute", ["code", "name"])
def test_ambiguous_catalog_is_rejected(attribute: str) -> None:
    first, second = (copy(rule) for rule in build_rule_set().rules[:2])
    setattr(second, attribute, getattr(first, attribute))

    with pytest.raises(ValueError, match=f"duplicate rule {attribute}"):
        RuleSet([first, second])


def test_lazy_selection_preserves_catalog_metadata() -> None:
    eager = build_rule_set()
    lazy = build_rule_set(lazy=True)
    assert {rule.code for rule in lazy.rules} == {rule.code for rule in eager.rules}
    assert lazy.rules
    for rule in lazy.rules:
        assert isinstance(rule, RuleContract)
        original = eager.by_code(rule.code)
        assert isinstance(original, Rule)
        assert isinstance(original, RuleContract)
        for attribute in (
            "name",
            "category",
            "default_severity",
            "requires",
            "scope",
            "enabled_by_default",
            "activation",
            "file_local",
            "test_local",
            "member_local",
        ):
            assert getattr(rule, attribute) == getattr(original, attribute)
        assert rule.explain() == original.explain()


def test_member_check_does_not_import_inactive_test_rules(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("def helper(): pass\n")
    (tmp_path / "smelt.yml").write_text(
        "version: 2\nproject: {root_packages: [app]}\nrules: {visibility/internal-public-member: warning}\n"
    )
    script = """
import sys
from smelt.cli import main
assert main(['check', '--no-cache']) == 0
assert 'smelt.rules.structure.members' in sys.modules
assert 'smelt.rules.testing.awaits' not in sys.modules
assert 'smelt.analysis.test_syntax' not in sys.modules
"""
    completed = subprocess.run(  # noqa: S603 - fixed Python regression script
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr


def _catalog_problems(rule: RuleContract) -> list[str]:
    family, _, finding = rule.name.partition("/")
    doc = rule.explain()
    checks = {
        "code must be the family prefix and three digits": re.fullmatch(
            rf"{_CODE_PREFIXES[rule.category]}\d{{3}}", rule.code
        ),
        "name must start with its family": family == rule.category.value,
        "finding must be kebab-case": re.fullmatch(
            r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", finding
        ),
        "finding must not use a retired qualifier": not finding.startswith(
            _RETIRED_QUALIFIERS
        ),
        "default severity must be a Severity": isinstance(
            rule.default_severity, Severity
        ),
        "requires must be a frozenset of Index": isinstance(rule.requires, frozenset)
        and all(isinstance(index, Index) for index in rule.requires),
        "RuleDoc needs summary, rationale, bad, good and fix": all(
            value.strip()
            for value in (doc.summary, doc.rationale, doc.bad, doc.good, doc.fix)
        ),
        "config paths must name SmeltConfig fields": all(
            path.split(".")[0] in SmeltConfig.model_fields for path in doc.config
        ),
    }
    return [message for message, passed in checks.items() if not passed]
