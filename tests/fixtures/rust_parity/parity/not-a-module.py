"""The file name is no identifier, so discovery must skip this file."""


def skipped(a, b, c, d):
    return a if b else c if d else a
