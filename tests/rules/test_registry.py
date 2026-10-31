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
from smelt.rules.base import BaseRule, RuleSet
from smelt.rules.registry import build_rule_set


class TestBuiltins:
    def test_every_public_rule_implementation_is_registered(self) -> None:
        implementations = set()
        for info in pkgutil.walk_packages(smelt.rules.__path__, "smelt.rules."):
            module = importlib.import_module(info.name)
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if (
                    cls.__module__ == info.name
                    and not name.startswith("_")
                    and cls is not BaseRule
                    and issubclass(cls, BaseRule)
                ):
                    implementations.add(cls)

        registered = {type(rule) for rule in build_rule_set().rules}
        assert implementations == registered, (
            "Register new rules in smelt/rules/registry.py; keep abstract helpers private",
            implementations ^ registered,
        )

    @pytest.mark.parametrize("rule", build_rule_set().rules, ids=lambda rule: rule.code)
    def test_catalog_contract(self, rule: BaseRule) -> None:
        family = {
            Category.DEPENDENCIES: "1",
            Category.STRUCTURE: "3",
            Category.TESTS: "4",
            Category.META: "9",
        }
        assert re.fullmatch(rf"SMT{family[rule.category]}\d{{2}}", rule.code)
        assert re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", rule.name)
        assert isinstance(rule.default_severity, Severity)
        assert isinstance(rule.requires, frozenset)
        assert all(isinstance(index, Index) for index in rule.requires)
        doc = rule.explain()
        for value in (doc.summary, doc.rationale, doc.bad, doc.good, doc.fix):
            assert value.strip(), f"{rule.code} needs complete RuleDoc content"
        for path in doc.config:
            assert path.split(".")[0] in SmeltConfig.model_fields, path

    def test_codes_are_unique(self) -> None:
        codes = [rule.code for rule in build_rule_set().rules]

        assert len(codes) == len(set(codes))

    def test_rules_are_sorted_by_code(self) -> None:
        rules = build_rule_set().rules

        assert [rule.code for rule in rules] == sorted(rule.code for rule in rules)

    def test_lookup_by_code_and_by_name(self) -> None:
        rule_set = build_rule_set()

        assert rule_set.lookup("smt101") is rule_set.by_code("SMT101")
        assert rule_set.lookup("layer-boundary") is rule_set.by_code("SMT101")
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
        original = eager.by_code(rule.code)
        assert original is not None
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
    (tmp_path / "smelt.yaml").write_text(
        "version: 1\nproject: {root_packages: [app]}\nrules: {internal-public-member: warning}\n"
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
