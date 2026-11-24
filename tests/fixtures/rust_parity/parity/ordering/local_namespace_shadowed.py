def _private():
    return 1


def public():
    def helper():
        return 1

    helper = 2
    return vars(helper)
