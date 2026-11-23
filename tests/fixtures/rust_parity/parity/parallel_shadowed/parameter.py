class Shadowing:
    left: list[int]
    right: list[int]

    def run(self, zip):
        return zip(self.left, self.right)
