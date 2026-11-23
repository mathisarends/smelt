# smelt: ignore-file[CPX003] -- the whole file keeps its positional signatures
"""A file-level suppression covers one code and leaves the others reported."""

a = b = c = d = e = 1
reported = a if b else c if d else e


def hidden(a, b, c, d):
    return a, b, c, d
