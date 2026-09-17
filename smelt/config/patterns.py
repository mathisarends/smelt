import re
from functools import cache


@cache
def _path_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(parts) + "(?:/.*)?")


def path_matches(pattern: str, path: str) -> bool:
    """Match a POSIX path against a glob; a match on a directory covers its contents."""
    return _path_regex(pattern.strip("/")).fullmatch(path) is not None


@cache
def _module_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    for index, segment in enumerate(pattern.split(".")):
        if segment == "**":
            parts.append(r"(?:\.[^.]+)*" if index else r"[^.]+(?:\.[^.]+)*")
            continue
        piece = re.escape(segment).replace(r"\*", "[^.]*")
        parts.append(rf"\.{piece}" if index else piece)
    return re.compile("".join(parts))


def module_matches(pattern: str, module: str) -> bool:
    """Match a dotted module name; ``*`` is one segment, ``**`` any number (including none)."""
    return _module_regex(pattern).fullmatch(module) is not None
