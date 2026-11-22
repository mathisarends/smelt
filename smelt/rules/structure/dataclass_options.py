from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.bindings import dotted_name
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import is_stdlib, tree_nodes
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.edits import SourceText

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

# These bases declare empty __slots__, so slots still pay off below them.
_SLOTTED_MODULES = frozenset({"abc", "typing", "typing_extensions"})


class MissingDataclassOption(Rule):
    file_local = True
    activation = POLICIES["MOD006"]
    code = "MOD006"
    name = "modeling/missing-dataclass-option"
    category = Category.MODELING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A dataclass does not set an option its package requires.",
        rationale=(
            "Whether a dataclass is frozen, uses slots or takes keyword-only "
            "arguments changes how callers may use it, yet codebases drift "
            "between @dataclass, @dataclass(frozen=True) and "
            "@dataclass(frozen=True, slots=True) without anyone being able to "
            "tell which deviations are deliberate. A style override lists "
            "dataclass_options, any of frozen, slots and kw_only, and every "
            "production @dataclass in the package must set each of them to "
            "True. An option that is missing or explicitly False is reported; "
            "a non-literal value is not judged. Missing slots is not reported "
            "when a base class comes from outside the project, such as a "
            "third-party or builtin class without __slots__, because slots "
            "save nothing there; bases from abc and typing still count. It is "
            "also not reported for classes with a functools.cached_property, "
            "which needs an instance __dict__. "
            "Nested packages can set their own list, and [] lifts the "
            "requirement. Tests are not checked."
        ),
        bad=("@dataclass(frozen=True)\nclass Policy:\n    timeout_seconds: float"),
        good=(
            "@dataclass(frozen=True, slots=True)\n"
            "class Policy:\n"
            "    timeout_seconds: float"
        ),
        fix=(
            "Add the missing options to the decorator. A dataclass that must "
            "stay mutable or unslotted keeps a reasoned suppression, or lives "
            "in a package whose contract sets its own dataclass_options. "
            "check --fix --unsafe-fixes sets the missing options to True, unless "
            "the decorator passes **options. Enable it for the project with "
            "style: {dataclass_options: [frozen, slots]}, or for one package "
            "with the same option in a style override."
        ),
        canonical="Set the dataclass options the package requires",
        config=("style.dataclass_options", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.policy_for(target.module, "dataclass_options")
        if contract is None or not contract.spec.dataclass_options:
            return None
        options = ", ".join(
            f"{option}=True" for option in contract.spec.dataclass_options
        )
        return f"Declare dataclasses as @dataclass({options})"

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        roots = frozenset(ctx.config.project.root_packages)
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "dataclass_options")
            if contract is None or not contract.spec.dataclass_options:
                continue
            required = contract.spec.dataclass_options
            tree = ctx.asts.parse(source.path)
            external = _external_names(tree, roots)
            text: SourceText | None = None
            for owner in tree_nodes(tree):
                if not isinstance(owner, ast.ClassDef):
                    continue
                missing = _missing(owner, required, external)
                if not missing:
                    continue
                options = ", ".join(f"{option}=True" for option in missing)
                text = text or SourceText(ctx.files.read_text(source.path))
                fix = _fix(text, owner, missing)
                yield self.violation(
                    f"Dataclass {owner.name} is missing {options}; "
                    f"{contract.origin} requires it",
                    path=source.path,
                    line=owner.lineno,
                    column=owner.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "contract": contract.name,
                        "class": owner.name,
                        "dataclass_options": list(required),
                        "missing": missing,
                        "automatic_rewrite": fix is not None,
                    },
                    hint=f"Add {options} to the @dataclass decorator of {owner.name}.",
                    fix=fix,
                )


@dataclass(frozen=True, slots=True)
class _ExternalNames:
    imported: frozenset[str]
    local: frozenset[str]

    def contains(self, base: ast.expr) -> bool:
        while isinstance(base, ast.Subscript | ast.Attribute):
            base = base.value
        if not isinstance(base, ast.Name):
            return False
        name = base.id
        if name in self.imported:
            return True
        return name not in self.local and name != "object" and hasattr(builtins, name)


def _missing(
    owner: ast.ClassDef, required: Sequence[str], external: _ExternalNames
) -> list[str]:
    keywords = _dataclass_keywords(owner)
    if keywords is None:
        return []
    missing: list[str] = []
    for option in required:
        value = keywords.get(option)
        if value is not None and not (
            isinstance(value, ast.Constant) and value.value is False
        ):
            continue
        if option == "slots" and (
            any(external.contains(base) for base in owner.bases)
            or _caches_properties(owner)
        ):
            continue
        missing.append(option)
    return missing


def _caches_properties(owner: ast.ClassDef) -> bool:
    # functools.cached_property stores its value in __dict__, which slots remove.
    return any(
        dotted_name(decorator).rpartition(".")[2] == "cached_property"
        for method in owner.body
        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in method.decorator_list
    )


def _dataclass_keywords(owner: ast.ClassDef) -> dict[str, ast.expr] | None:
    decorator = _dataclass_decorator(owner)
    if decorator is None:
        return None
    return {
        item.arg: item.value
        for item in (decorator.keywords if isinstance(decorator, ast.Call) else [])
        if item.arg is not None
    }


def _fix(source: SourceText, owner: ast.ClassDef, missing: list[str]) -> Fix | None:
    """Set each missing option to True on the dataclass decorator.

    Unsafe: freezing, slotting or keyword-only construction breaks callers that
    mutate instances, add attributes or pass fields positionally.
    """
    decorator = _dataclass_decorator(owner)
    if decorator is None:
        return None
    if not isinstance(decorator, ast.Call):
        listed = ", ".join(f"{option}=True" for option in missing)
        edits = [source.insert(source.span(decorator).end, f"({listed})")]
    else:
        if any(keyword.arg is None for keyword in decorator.keywords):
            return None
        present = {keyword.arg: keyword.value for keyword in decorator.keywords}
        edits = [
            source.replace(present[option], "True")
            for option in missing
            if option in present
        ]
        added = [f"{option}=True" for option in missing if option not in present]
        arguments: list[ast.expr | ast.keyword] = [
            *decorator.args,
            *decorator.keywords,
        ]
        if added and arguments:
            end = max(source.span(argument).end for argument in arguments)
            edits.append(source.insert(end, ", " + ", ".join(added)))
        elif added:
            end = source.span(decorator).end
            if source.text[end - 1] != ")":
                return None
            edits.append(source.insert(end - 1, ", ".join(added)))
    options = ", ".join(f"{option}=True" for option in missing)
    return Fix(Applicability.UNSAFE, f"Set {options} on {owner.name}", tuple(edits))


def _dataclass_decorator(owner: ast.ClassDef) -> ast.expr | None:
    for decorator in owner.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if dotted_name(target).rpartition(".")[2] == "dataclass":
            return decorator
    return None


def _external_names(tree: ast.Module, roots: frozenset[str]) -> _ExternalNames:
    imported: set[str] = set()
    local: set[str] = set()
    for node in tree_nodes(tree):
        match node:
            case ast.ImportFrom(module=module, level=0, names=names) if module:
                target = imported if _is_external(module, roots) else local
                target.update(alias.asname or alias.name for alias in names)
            case ast.ImportFrom(names=names):
                local.update(alias.asname or alias.name for alias in names)
            case ast.Import(names=names):
                for alias in names:
                    bound = alias.asname or alias.name.partition(".")[0]
                    external = _is_external(alias.name, roots)
                    (imported if external else local).add(bound)
            case ast.ClassDef(name=name) | ast.Assign(targets=[ast.Name(id=name)]):
                local.add(name)
    return _ExternalNames(frozenset(imported), frozenset(local))


def _is_external(module: str, roots: frozenset[str]) -> bool:
    top = module.partition(".")[0]
    if top in roots:
        return False
    return not (is_stdlib(module) and top in _SLOTTED_MODULES)
