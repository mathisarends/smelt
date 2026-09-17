import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class ConfigError(Exception):
    def __init__(
        self, issues: Sequence[ConfigIssue], source: str | None = None
    ) -> None:
        self.issues = tuple(issues)
        self.source = source
        header = (
            f"invalid configuration in {source}" if source else "invalid configuration"
        )
        lines = [header, *(f"  {issue}" for issue in self.issues)]
        super().__init__("\n".join(lines))


def format_loc(loc: Iterable[str | int]) -> str:
    """Render a location tuple as ``a.b[1].c``."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        elif parts:
            parts.append(f".{item}")
        else:
            parts.append(item)
    return "".join(parts)


def did_you_mean(value: str, candidates: Iterable[str]) -> str:
    options = sorted(candidates)
    matches = difflib.get_close_matches(value, options, n=1, cutoff=0.6)
    if not matches:
        matches = [option for option in options if option.startswith(value)][:1]
    return f' (did you mean "{matches[0]}"?)' if matches else ""
