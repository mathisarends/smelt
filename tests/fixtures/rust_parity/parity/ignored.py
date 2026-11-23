"""An ignore entry hides one rule for this path; the other rule still reports."""

a = b = c = d = e = 1
reported = a if b else c if d else e


def hidden(a, b, c, d):
    return a, b, c, d
