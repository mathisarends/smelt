"""Tests no enabled rule reads keep their suppressions judged."""


def test_private():  # smelt: ignore[TST002] -- reaches into a private helper
    assert True


def test_reasonless():  # smelt: ignore[tests/private-access]
    assert True
