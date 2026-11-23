from builtins import staticmethod as static_alias


class Aliased:
    @static_alias
    def method(self, a, b):
        self.a, self.b = a, b
