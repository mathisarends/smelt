import importlib
import inspect
import pkgutil
import re
from copy import copy

import pytest

import smelt.rules
from smelt.analysis.context import Index
from smelt.config.models import SmeltConfig
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import BaseRule, RuleSet
from smelt.rules.registry import build_rule_set, builtin_rules


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

        registered = {type(rule) for rule in builtin_rules()}
        assert implementations == registered, (
            "Register new rules in smelt/rules/registry.py; keep abstract helpers private",
            implementations ^ registered,
        )

    @pytest.mark.parametrize("rule", builtin_rules(), ids=lambda rule: rule.code)
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
        codes = [rule.code for rule in builtin_rules()]

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
    first, second = (copy(rule) for rule in builtin_rules()[:2])
    setattr(second, attribute, getattr(first, attribute))

    with pytest.raises(ValueError, match=f"duplicate rule {attribute}"):
        RuleSet([first, second])
