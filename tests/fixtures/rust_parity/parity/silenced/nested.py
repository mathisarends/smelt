"""The nested conditional is off here; the parameter limit still applies."""

a = b = c = d = e = 1
quiet = a if b else c if d else e


def reported(a, b, c):
    return a, b, c
