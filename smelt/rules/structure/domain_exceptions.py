from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.common import readable_list


class PreferDomainException(Rule):
    activation = POLICIES["MOD008"]
    code = "MOD008"
    name = "modeling/prefer-domain-exception"
    category = Category.MODELING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Production code raises a builtin exception instead of the project hierarchy.",
        rationale=(
            "A project exception hierarchy lets callers tell a domain failure "
            "apart from a programming error. Code that keeps raising "
            "ValueError or RuntimeError for domain failures, or converts "
            "every error into one, forces callers to catch broad builtins and "
            "guess what went wrong. A style override names the project "
            "base, e.g. errors: {base: app.errors.AppError}, and production "
            "code in the package that raises a builtin exception class, with "
            "or without arguments and with or without from, is reported. "
            "NotImplementedError, StopIteration, StopAsyncIteration, "
            "GeneratorExit, KeyboardInterrupt and SystemExit are always "
            "allowed, and errors.allow adds more, e.g. [TypeError]. Bare "
            "re-raises, raising caught or project exceptions, names that "
            "shadow a builtin, raises in protocol dunder methods such as "
            "__getattr__ or __getitem__, and pydantic validators, which must "
            "raise ValueError or AssertionError, are not reported. Tests are "
            "not checked."
        ),
        bad=(
            "try:\n"
            "    return self._read(path)\n"
            "except Exception as error:  # surface unreadable files\n"
            '    raise ValueError(f"Could not read {path.name}") from error'
        ),
        good=(
            "from app.errors import ReadError\n\n"
            "try:\n"
            "    return self._read(path)\n"
            "except Exception as error:  # surface unreadable files\n"
            '    raise ReadError(f"Could not read {path.name}") from error'
        ),
        fix=(
            "Raise a subclass of the configured base that names the failure, "
            "and let callers catch that subclass. When callers rely on a "
            "builtin, for example TypeError for wrong argument types, add it "
            "to errors.allow, or set errors: {} in a nested package that "
            "deliberately raises builtins. No automatic rewrite is performed. "
            "Enable it for the project with style: {errors: {base: "
            "app.errors.AppError}}, or for one package with the same option in a "
            "style override."
        ),
        canonical=(
            "Raise the project's exception hierarchy instead of builtin exceptions"
        ),
        config=("style.errors", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.policy_for(target.module, "errors")
        errors = contract.spec.errors if contract else None
        if errors is None or errors.base is None:
            return None
        allowed = f" (except {readable_list(errors.allow)})" if errors.allow else ""
        return (
            f"Raise subclasses of {errors.base} instead of builtin exceptions{allowed}"
        )
