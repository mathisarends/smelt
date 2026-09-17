from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

SOURCE = """
class Money:
    def __init__(self, cents: int) -> None:
        self.cents = cents

    def add(self, other: "Money") -> "Money":
        return Money(self.cents + other.cents)

    @classmethod
    def zero(cls) -> Money:
        return Money(0)

    @staticmethod
    def parse(text: str) -> "Money":
        return Money(int(text))

    def fine(self) -> "list[Money]":
        return [type(self)(1)]
"""


def _project(tmp_path: Path, config: str = "") -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n" + config,
            "app/__init__.py": "",
            "app/money.py": SOURCE,
        },
    )


class TestSelfClassReference:
    def test_opt_in_rule_is_off_by_default(self, tmp_path: Path) -> None:
        assert violations(_project(tmp_path), CheckOptions(select=("SMT2",))) == []

    def test_enabled_through_config(self, tmp_path: Path) -> None:
        root = _project(tmp_path, "rules:\n  SMT206: warning\n")

        found = violations(root)

        assert [(v.line, v.message) for v in found] == [
            (5, "Money.add is annotated to return Money; use Self"),
            (6, "Money.add constructs Money(...); use type(self)(...)"),
            (9, "Money.zero is annotated to return Money; use Self"),
            (10, "Money.zero constructs Money(...); use cls(...)"),
        ]
