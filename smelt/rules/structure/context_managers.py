from smelt.analysis.context import Index
from smelt.diagnostics.fixes import (
    FixAvailability,
)
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class DeprecatedAsyncContextmanagerReturn(Rule):
    code = "TYP004"
    name = "typing/deprecated-async-contextmanager-return"
    category = Category.TYPING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An @asynccontextmanager function annotates its return as AsyncIterator[T].",
        rationale="The contextlib.asynccontextmanager decorator remains supported. Typeshed deprecates its AsyncIterator overload because the decorator consumes an async generator, including its athrow/aclose methods. Use AsyncGenerator[T, None] to express that contract explicitly; the second argument also works on Python 3.12. This opt-in check provides the same guidance without requiring a type checker. It recognizes async functions and methods with direct AsyncIterator[T] annotations from collections.abc or typing, including quoted annotations, module/import aliases and guarded module-level imports such as TYPE_CHECKING. It conservatively skips names rebound anywhere in the file, wildcard imports, local imports, re-exports, user-defined types, type aliases and wrapped annotations. Ordinary async iterators and functions already annotated as AsyncGenerator remain valid. See https://github.com/python/typeshed/blob/main/stdlib/contextlib.pyi for the deprecated overload.",
        bad="from collections.abc import AsyncIterator\nfrom contextlib import asynccontextmanager\n\n@asynccontextmanager\nasync def exclusive() -> AsyncIterator[None]:\n    yield",
        good="from collections.abc import AsyncGenerator\nfrom contextlib import asynccontextmanager\n\n@asynccontextmanager\nasync def exclusive() -> AsyncGenerator[None, None]:\n    yield",
        fix="Replace AsyncIterator[T] with AsyncGenerator[T, None] and import AsyncGenerator from collections.abc. Preserve the yielded type and the decorator, and remove the AsyncIterator import only if unused elsewhere. check --fix rewrites the annotation, reuses or adds the AsyncGenerator import from the same module and removes a module-level AsyncIterator import that nothing else names. Enable rules: {typing/deprecated-async-contextmanager-return: warning}, or select TYP004. Package overrides and reasoned inline suppressions are supported.",
        canonical=(
            "Annotate @asynccontextmanager functions as AsyncGenerator[T, None]"
        ),
        config=("rules", "style.overrides"),
    )
