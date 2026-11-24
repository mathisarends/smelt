def _private():
    return 1


def public():
    global _private
    return 1
