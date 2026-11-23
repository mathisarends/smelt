from helpers import *


class Wildcard:
    left: list[int]
    right: list[int]

    def run(self):
        return zip(self.left, self.right)
