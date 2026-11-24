"""Comments travel with definitions."""

# Leading comment of the private helper.
def _helper():
    return 1
    # Trailing comment inside the helper.

# Leading comment of the public function.
def public():
    return _helper()


class Holder:
    # Comment before the private method.
    def _private(self):
        return 1

    def public(self):
        return 2
