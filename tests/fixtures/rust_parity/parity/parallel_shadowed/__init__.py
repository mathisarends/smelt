from helpers import zip


class Imported:
    left: list[int]
    right: list[int]

    def run(self):
        return zip(self.left, self.right)
