"""MOD011 and CPX008 skip files with wildcard imports."""

from dataclasses import dataclass
from os.path import *


@dataclass(frozen=True)
class Unknown:
    values: list[int]


class Local:
    def run(self):
        return self


result = Local().run()
