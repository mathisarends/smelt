"""Reflection hides uses."""
LIMIT = 1


def lookup(name):
    return globals()[name]
