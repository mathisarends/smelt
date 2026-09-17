import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Category
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.registry import (
    ENTRY_POINT_GROUP,
    PluginError,
    build_rule_set,
    builtin_rules,
)

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.violation import Violation

PLUGIN = """
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import BaseRule, RuleDoc


class {name}(BaseRule):
    code = "{code}"
    name = "{rule}"
    category = Category.CODE
    default_severity = Severity.WARNING
    doc = RuleDoc(
        summary="A house rule.",
        rationale="Because we say so.",
        bad="x = 1",
        good="x = 2",
        fix="Write 2.",
    )

    def check(self, ctx):
        return []


RULES = [{name}]
"""


def _write_plugin(
    tmp_path: Path, module: str, *, code: str = "ACME001", body: str | None = None
) -> Path:
    source = body or PLUGIN.format(name="HouseRule", code=code, rule="house-rule")
    (tmp_path / f"{module}.py").write_text(
        textwrap.dedent(source).lstrip("\n"), encoding="utf-8"
    )
    return tmp_path


def _load(tmp_path: Path, module: str) -> list[str]:
    rule_set = build_rule_set(
        [module], search_paths=[tmp_path], load_entry_points=False
    )
    return [rule.code for rule in rule_set.rules]


class TestBuiltins:
    def test_codes_are_unique(self) -> None:
        codes = [rule.code for rule in builtin_rules()]

        assert len(codes) == len(set(codes))

    def test_rules_are_sorted_by_code(self) -> None:
        rules = build_rule_set(load_entry_points=False).rules

        assert [rule.code for rule in rules] == sorted(rule.code for rule in rules)

    def test_lookup_by_code_and_by_name(self) -> None:
        rule_set = build_rule_set(load_entry_points=False)

        assert rule_set.lookup("smt101") is rule_set.by_code("SMT101")
        assert rule_set.lookup("layer-boundary") is rule_set.by_code("SMT101")
        assert rule_set.lookup("nope") is None


class TestPluginModules:
    def test_rules_are_registered(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "acme_rules")

        assert "ACME001" in _load(tmp_path, "acme_rules")

    def test_unknown_module(self, tmp_path: Path) -> None:
        with pytest.raises(PluginError, match='cannot import plugin "missing_rules"'):
            _load(tmp_path, "missing_rules")

    def test_module_without_rules(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "empty_rules", body="VALUE = 1\n")

        with pytest.raises(PluginError, match="does not define RULES"):
            _load(tmp_path, "empty_rules")

    def test_object_that_is_not_a_rule(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "bogus_rules", body="RULES = ['nope']\n")

        with pytest.raises(PluginError, match="does not implement the Rule protocol"):
            _load(tmp_path, "bogus_rules")

    def test_code_that_is_already_taken(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "clashing_rules", code="SMT101")

        with pytest.raises(PluginError, match='"SMT101" is already registered'):
            _load(tmp_path, "clashing_rules")

    def test_search_path_is_removed_again(self, tmp_path: Path) -> None:
        _write_plugin(tmp_path, "tidy_rules")

        _load(tmp_path, "tidy_rules")

        assert str(tmp_path) not in sys.path


class _EntryPointRule(BaseRule):
    code = "ACME900"
    name = "entry-point-rule"
    category = Category.CODE
    doc = RuleDoc(
        summary="A house rule.",
        rationale="Because we say so.",
        bad="x = 1",
        good="x = 2",
        fix="Write 2.",
    )

    def check(self, ctx: AnalysisContext) -> list[Violation]:
        return []


class _FakeEntryPoint:
    name = "acme"

    def __init__(self, value: object) -> None:
        self._value = value

    def load(self) -> object:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class TestEntryPoints:
    def _patch(
        self, monkeypatch: pytest.MonkeyPatch, *entries: _FakeEntryPoint
    ) -> None:
        def fake(group: str) -> tuple[_FakeEntryPoint, ...]:
            return entries if group == ENTRY_POINT_GROUP else ()

        monkeypatch.setattr("smelt.rules.registry.entry_points", fake)

    def test_rules_from_an_entry_point_are_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch(monkeypatch, _FakeEntryPoint(_EntryPointRule))

        codes = [rule.code for rule in build_rule_set().rules]

        assert codes[0] == "ACME900"

    def test_a_failing_entry_point_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch(monkeypatch, _FakeEntryPoint(RuntimeError("boom")))

        with pytest.raises(PluginError, match='cannot load entry point "acme": boom'):
            build_rule_set()

    def test_entry_points_can_be_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, _FakeEntryPoint(_EntryPointRule))

        codes = [rule.code for rule in build_rule_set(load_entry_points=False).rules]

        assert "ACME900" not in codes
