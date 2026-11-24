"""A module whose export list we cannot read."""
__all__ = ["a"] + ["b"]


def maybe_public():
    return 1
