from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class MissingBroadExceptReason(Rule):
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
        canonical=(
            "Catch specific exceptions; comment why a broad except handler is necessary"
        ),
        config=("rules",),
    )
