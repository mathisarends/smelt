from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.diagnostics.violation import Violation

_PATTERN = re.compile(
    r"#\s*smelt:\s*(?P<kind>ignore-file|ignore)\b"
    r"(?:\[(?P<codes>[^\]]*)\])?"
    r"(?:\s*--\s*(?P<reason>.*))?"
)


@dataclass(eq=False)
class Suppression:
    path: str
    line: int
    column: int  # 0-based start of the comment
    file_level: bool
    codes: tuple[str, ...]  # empty: every code
    reason: str | None
    start_line: int | None = None
    end_line: int | None = None
    used_codes: set[str] = field(default_factory=set)

    def covers(self, code: str) -> bool:
        return not self.codes or any(code.startswith(prefix) for prefix in self.codes)

    def matches(self, violation: Violation) -> bool:
        if violation.path != self.path or not self.covers(violation.code):
            return False
        return (
            self.file_level
            or violation.line == self.line
            or (
                self.start_line is not None
                and self.end_line is not None
                and violation.line is not None
                and self.start_line <= violation.line <= self.end_line
            )
        )

    def mark_used(self, code: str) -> None:
        self.used_codes.add(code)

    def unused_codes(self) -> tuple[str, ...]:
        return tuple(
            prefix
            for prefix in self.codes
            if not any(code.startswith(prefix) for code in self.used_codes)
        )

    @property
    def is_used(self) -> bool:
        return bool(self.used_codes)


def parse_suppressions(path: str, source: str) -> list[Suppression]:
    found: list[Suppression] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return found
    statements: list[tuple[int, int, bool]] = []
    start: int | None = None
    import_statement = False
    for token in tokens:
        if token.type == tokenize.NEWLINE:
            if start is not None:
                statements.append((start, token.start[0], import_statement))
                start = None
        elif (
            token.type
            not in {
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
            }
            and start is None
        ):
            start = token.start[0]
            import_statement = token.string in {"from", "import"}
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        match = _PATTERN.search(token.string)
        if match is None:
            continue
        codes = tuple(
            code.strip().upper()
            for code in (match.group("codes") or "").split(",")
            if code.strip()
        )
        reason = (match.group("reason") or "").strip() or None
        # Inline exceptions follow their logical statement through formatting.
        # Interior import-name comments stay local to the affected name's line.
        span = next(
            (
                (first, last)
                for first, last, is_import in statements
                if first <= token.start[0] <= last
                and (
                    not is_import
                    or token.start[0] == first
                    or (
                        token.start[0] == last
                        and token.line[: token.start[1]]
                        .lstrip()
                        .startswith((")", "]", "}"))
                    )
                )
            ),
            (None, None),
        )
        found.append(
            Suppression(
                path=path,
                line=token.start[0],
                column=token.start[1] + match.start(),
                file_level=match.group("kind") == "ignore-file",
                codes=codes,
                reason=reason,
                start_line=span[0],
                end_line=span[1],
            )
        )
    return found
