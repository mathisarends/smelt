def _private():
    return 1


def public():
    def helper():
        return 1

    vars(helper)
    return helper.__dict__
