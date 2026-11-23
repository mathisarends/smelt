from parity.test_support.service import _normalize


def test_not_a_test_file():
    _normalize("A")
