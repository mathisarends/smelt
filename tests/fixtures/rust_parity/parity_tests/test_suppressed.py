from parity.test_support.service import _normalize  # smelt: ignore[TST002] -- kept


def test_line_suppression():  # smelt: ignore[tests/missing-outcome-check] -- kept
    _normalize("A")


def test_unsuppressed():
    _normalize("A")
