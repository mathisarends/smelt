from smelt.diagnostics.legacy_codes import LEGACY_CODES, legacy_code
from smelt.rules.registry import build_rule_set


def test_every_smt_code_names_a_distinct_current_rule() -> None:
    codes = {rule.code for rule in build_rule_set().rules}

    assert set(LEGACY_CODES.values()) <= codes
    assert len(set(LEGACY_CODES.values())) == len(LEGACY_CODES)


def test_legacy_code_maps_back_and_leaves_new_codes_alone() -> None:
    assert (legacy_code("ARC001"), legacy_code("NEW001")) == ("SMT101", "NEW001")
