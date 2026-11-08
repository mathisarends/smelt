"""Named selections for rules enforcing one coherent maintenance policy."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleGroup:
    name: str
    summary: str
    codes: tuple[str, ...]
    rationale: str


RULE_GROUPS = {
    "information-hiding": RuleGroup(
        name="information-hiding",
        summary="Keep implementation details private, including APIs exposed only for tests.",
        codes=("SMT316", "SMT320", "SMT335", "SMT429", "SMT430"),
        rationale=(
            "Review unnecessarily public functions, methods, module constants/type aliases and data attributes. Test-only consumers do not justify public visibility; the current test-only reviews cover module functions and data attributes. Methods with observed test users retain SMT316's external-use exclusion. Each diagnostic retains its own evidence, conservative exclusions and stable SMT code. This selection does not include moving private class constants (SMT334) or extracting static helpers (SMT317): those are placement conventions. A group severity supplies the default for its members; explicit individual settings in the same map override it regardless of key order. Closest-package overrides still win. Existing individual names, suppressions and debt identities remain valid."
        ),
    ),
}
