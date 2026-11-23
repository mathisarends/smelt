"""The relaxed contract allows four positional parameters."""


def allowed(a, b, c, d):
    return a, b, c, d


def too_many(a, b, c, d, e):
    return a, b, c, d, e
