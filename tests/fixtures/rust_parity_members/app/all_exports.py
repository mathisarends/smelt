"""A module with an explicit export list."""
__all__ = ["listed"]


def listed():
    return 1


def unlisted():
    return listed()
