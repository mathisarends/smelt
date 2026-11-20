from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_BROAD = frozenset({"Exception", "BaseException"})
_BARE = "bare"
# Directives that only steer a tool; text after them still counts as a reason.
_DIRECTIVE = re.compile(
    r"""^\s*(?:
        noqa(?::\s*[A-Z]+[0-9]+(?:[\s,]+[A-Z]+[0-9]+)*)?
      | pylint:\s*disable(?:-next)?=[\w-]+(?:\s*,\s*[\w-]+)*
      | (?:type|pyright):\s*ignore(?:\[[^\]]*\])?
      | pragma:\s*no\s+(?:cover|branch)
      | smelt:\s*ignore(?:-file)?(?:\[[^\]]*\])?
      | fmt:\s*(?:on|off|skip)
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_SEPARATORS = " \t-:;,."


class MissingBroadExceptReason(Rule):
    file_local = True
    code = "IDM006"
    name = "idioms/missing-broad-except-reason"
    category = Category.IDIOMS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A broad exception handler does not say why it catches everything.",
        rationale=(
            "Catching Exception, BaseException or everything with a bare except "
            "is sometimes the right call: a background task must survive a "
            "failing callback, or a best-effort lookup may fail in many ways. "
            "Which of these applies is invisible in the code itself, and a "
            "linter pragma such as '# noqa: BLE001' or '# pylint: "
            "disable=broad-exception-caught' records only that the warning was "
            "silenced, not why. Without the reason, a reviewer cannot tell a "
            "deliberate boundary from a swallowed bug. This opt-in review "
            "reports an except clause whose type is Exception, BaseException, "
            "a tuple containing one of them, or missing, when no explanatory "
            "comment sits on the except line, on the comment lines directly "
            "above it, or between the except line and the first statement of "
            "its body, including a trailing comment on that statement. Tool "
            "directives such as noqa, pylint: disable, type: ignore, pragma: no "
            "cover and smelt: ignore do not count as an explanation, but text "
            "after them does. Handlers whose last statement is a raise and "
            "that contain no return, break or continue always propagate the "
            "failure and are not reported. Tests are not reported."
        ),
        bad=(
            "try:\n"
            "    return plugin.load()\n"
            "except Exception:  # pylint: disable=broad-exception-caught\n"
            "    return None"
        ),
        good=(
            "try:\n"
            "    return plugin.load()\n"
            "except Exception:  # pylint: disable=broad-exception-caught\n"
            "    # Third-party plugins may fail in any way; a broken one is skipped.\n"
            "    return None"
        ),
        fix=(
            "Catch the specific exceptions the code can recover from. When the "
            "handler must stay broad, add a comment that states why, for example "
            "which boundary it protects or why any failure is acceptable. "
            "No automatic rewrite is performed. Enable rules: "
            "{idioms/missing-broad-except-reason: hint}, or select IDM006. Package "
            "overrides and reasoned inline suppressions are supported."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            handlers = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler)
                and _broad_type(node) is not None
                and not _always_raises(node)
            ]
            if not handlers:
                continue
            comments = _Comments.scan(ctx.files.read_text(source.path))
            for handler in sorted(handlers, key=lambda item: item.lineno):
                if comments.explains(handler):
                    continue
                caught = _broad_type(handler) or _BARE
                subject = "Bare except" if caught == _BARE else f"Handler for {caught}"
                yield self.violation(
                    f"{subject} does not say why it catches every failure",
                    path=source.path,
                    line=handler.lineno,
                    column=handler.col_offset + 1,
                    source_module=source.module,
                    expected={"caught": caught, "automatic_rewrite": False},
                    hint=(
                        "Catch the specific exceptions you can handle, or add "
                        "a comment explaining why this handler must be broad."
                    ),
                )


@dataclass(frozen=True, slots=True)
class _Comments:
    texts: dict[int, tuple[str, ...]]
    standalone: frozenset[int]

    @classmethod
    def scan(cls, source: str) -> _Comments:
        texts: dict[int, list[str]] = {}
        standalone: set[int] = set()
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except (tokenize.TokenError, SyntaxError):
            tokens = []
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            line = token.start[0]
            texts.setdefault(line, []).append(token.string)
            if not token.line[: token.start[1]].strip():
                standalone.add(line)
        return cls(
            {line: tuple(items) for line, items in texts.items()},
            frozenset(standalone),
        )

    def explains(self, handler: ast.ExceptHandler) -> bool:
        # The except header, any lines up to the first body statement, and a
        # trailing comment on that statement.
        last = handler.body[0].lineno
        lines = list(range(handler.lineno, last + 1))
        above = handler.lineno - 1
        while above in self.standalone:
            lines.append(above)
            above -= 1
        return any(
            _is_reason(text) for line in lines for text in self.texts.get(line, ())
        )


def _is_reason(comment: str) -> bool:
    for segment in comment.split("#"):
        rest = segment
        while match := _DIRECTIVE.match(rest):
            rest = rest[match.end() :].lstrip(_SEPARATORS)
        if any(character.isalnum() for character in rest):
            return True
    return False


def _broad_type(handler: ast.ExceptHandler) -> str | None:
    if handler.type is None:
        return _BARE
    candidates = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    )
    for candidate in candidates:
        match candidate:
            case ast.Name(id=name) if name in _BROAD:
                return name
            case ast.Attribute(value=ast.Name(id="builtins"), attr=name) if (
                name in _BROAD
            ):
                return name
    return None


def _always_raises(handler: ast.ExceptHandler) -> bool:
    if not isinstance(handler.body[-1], ast.Raise):
        return False
    return not any(
        isinstance(node, ast.Return | ast.Break | ast.Continue)
        for node in _local_nodes(handler.body)
    )


def _local_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    # Statements inside nested functions and classes leave their own scope.
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))
