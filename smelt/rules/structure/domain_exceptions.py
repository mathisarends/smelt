from __future__ import annotations

import ast
import builtins
from typing import TYPE_CHECKING

from smelt.analysis.bindings import qualified_name
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from smelt.analysis.contracts import ResolvedContract
    from smelt.analysis.files import SourceFile

# Control flow rather than failures, and the abstract-method placeholder.
DEFAULT_ALLOWED = frozenset(
    {
        "NotImplementedError",
        "StopIteration",
        "StopAsyncIteration",
        "GeneratorExit",
        "KeyboardInterrupt",
        "SystemExit",
    }
)
_BUILTIN_EXCEPTIONS = frozenset(
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
)
# Dunders whose callers expect specific builtin exceptions by protocol.
_DOMAIN_DUNDERS = frozenset({"__init__", "__post_init__", "__new__", "__call__"})
_VALIDATORS = frozenset(
    {
        "pydantic.field_validator",
        "pydantic.model_validator",
        "pydantic.validator",
        "pydantic.root_validator",
        "pydantic.functional_validators.field_validator",
        "pydantic.functional_validators.model_validator",
    }
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


class PreferDomainException(Rule):
    file_local = True
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

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "errors")
            if contract is None or contract.spec.errors is None:
                continue
            if contract.spec.errors.base is None:
                continue
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            allowed = DEFAULT_ALLOWED | set(contract.spec.errors.allow)
            shadowed = set(syntax.bindings) | _module_names(syntax.tree)
            raises = sorted(
                _raises(syntax.tree.body, syntax.bindings),
                key=lambda node: (node.lineno, node.col_offset),
            )
            for raised in raises:
                name = _builtin(raised.exc, shadowed, syntax.bindings)
                if name is None or name in allowed:
                    continue
                yield self._finding(
                    contract,
                    source,
                    raised=raised,
                    name=name,
                    base=contract.spec.errors.base,
                )

    def _finding(
        self,
        contract: ResolvedContract,
        source: SourceFile,
        *,
        raised: ast.Raise,
        name: str,
        base: str,
    ) -> Violation:
        module, _, short = base.rpartition(".")
        return self.violation(
            f"Raise of builtin {name}; {contract.origin} expects a subclass of {base}",
            path=source.path,
            line=raised.lineno,
            column=raised.col_offset + 1,
            source_module=source.module,
            expected={
                "contract": contract.name,
                "exception": name,
                "base": base,
                "automatic_rewrite": False,
            },
            hint=(
                f"Raise a subclass of {short} from {module} that names the "
                f"failure, or add {name} to errors.allow when callers rely on it."
            ),
        )


def _raises(body: list[ast.stmt], bindings: Mapping[str, str]) -> Iterator[ast.Raise]:
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, _FUNCTIONS) and _exempt(node, bindings):
            continue
        if isinstance(node, ast.Raise) and node.exc is not None:
            yield node
        pending.extend(ast.iter_child_nodes(node))


def _exempt(
    function: ast.FunctionDef | ast.AsyncFunctionDef, bindings: Mapping[str, str]
) -> bool:
    name = function.name
    if name.startswith("__") and name.endswith("__") and name not in _DOMAIN_DUNDERS:
        return True
    return any(
        qualified_name(
            decorator.func if isinstance(decorator, ast.Call) else decorator, bindings
        )
        in _VALIDATORS
        for decorator in function.decorator_list
    )


def _builtin(
    exc: ast.expr | None, shadowed: set[str], bindings: Mapping[str, str]
) -> str | None:
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        if exc.id in _BUILTIN_EXCEPTIONS and exc.id not in shadowed:
            return exc.id
        return None
    qualified = qualified_name(exc, bindings) if exc is not None else None
    if qualified is None or not qualified.startswith("builtins."):
        return None
    name = qualified.removeprefix("builtins.")
    return name if name in _BUILTIN_EXCEPTIONS else None


def _module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, *_FUNCTIONS)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    return names
