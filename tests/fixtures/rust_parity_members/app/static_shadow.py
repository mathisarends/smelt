"""Shadowing staticmethod makes the decorator unknown."""
staticmethod = staticmethod


class Holder:
    @staticmethod
    def helper(value):
        return value
