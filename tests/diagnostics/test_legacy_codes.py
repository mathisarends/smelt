from smelt.diagnostics.legacy_codes import (
    LEGACY_CODES,
    legacy_code,
    legacy_prefix_codes,
    matches_code_prefix,
)
from smelt.rules.registry import build_rule_set


def test_every_smt_code_names_a_distinct_current_rule() -> None:
    codes = {rule.code for rule in build_rule_set().rules}

    assert set(LEGACY_CODES.values()) <= codes
    assert len(set(LEGACY_CODES.values())) == len(LEGACY_CODES)


def test_legacy_code_maps_back_and_leaves_new_codes_alone() -> None:
    assert (legacy_code("ARC001"), legacy_code("NEW001")) == ("SMT101", "NEW001")


def test_smt_prefix_selects_the_codes_it_used_to() -> None:
    assert legacy_prefix_codes("smt10") == tuple(
        LEGACY_CODES[f"SMT10{digit}"] for digit in range(1, 10)
    )
    assert legacy_prefix_codes("ARC") == ()


def test_prefix_matches_the_family_or_the_smt_code() -> None:
    assert [
        matches_code_prefix("TST001", prefix) for prefix in ("TST", "SMT4", "SMT3")
    ] == [True, True, False]
