"""Identifiers that name no current rule."""

TYPO = 1  # smelt: ignore[complexity/excess-positonal-parameters] -- misspelled
RETIRED = 2  # smelt: ignore[complexity/deep-object-navigation] -- renamed in 0.13.0
MERGED = 3  # smelt: ignore[SMT325] -- merged code
LEGACY = 4  # smelt: ignore[SMT901] -- legacy code
OLD_FAMILY = 5  # smelt: ignore[SMT999] -- an SMT code that never existed
HIDING = 6  # smelt: ignore[information-hiding] -- removed selector
NONSENSE = 7  # smelt: ignore[XYZ123, CPX003] -- unknown next to a known code
NAME_ONLY = 8  # smelt: ignore[made-up-rule]
