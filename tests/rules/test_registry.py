from smelt.rules.registry import build_rule_set, builtin_rules


class TestBuiltins:
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
