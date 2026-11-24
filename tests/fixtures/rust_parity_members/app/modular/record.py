"""Attributes under a module boundary."""


class Record:
    def __init__(self):
        self.name = "x"
        self.size = 1


def describe(record: Record):
    return record.name
