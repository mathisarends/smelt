from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.bindings import qualified_name
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class PreferProjectBaseClass(Rule):
    file_local = True
    activation = POLICIES["SMT353"]
    code = "SMT353"
    name = "prefer-project-base-class"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A class inherits an outside base directly instead of the project base.",
        rationale=(
            "Projects often wrap a framework base in their own base class to "
            "share configuration, for example a pydantic BaseModel subclass "
            "that sets extra='forbid' and frozen=True. A class that inherits "
            "the framework base directly silently misses that configuration, "
            "and nothing in the class tells a reviewer. A package contract "
            "maps each outside base to the project base, e.g. bases: "
            "{pydantic.BaseModel: app.core.Options}, and production classes in "
            "the package that list the outside base among their direct bases "
            "are reported. Bases are resolved through the module's imports, "
            "including aliases and module attributes. Only inheritance is "
            "judged: importing the outside base for annotations, isinstance "
            "checks or adapters stays valid. The module that defines the "
            "project base is exempt, a nested package can set bases: {} to "
            "allow direct inheritance, and single classes can keep a reasoned "
            "suppression. Tests are not checked."
        ),
        bad=(
            "from pydantic import BaseModel, ConfigDict\n\n"
            "class Manifest(BaseModel):\n"
            '    model_config = ConfigDict(extra="forbid")'
        ),
        good=(
            "from app.core.options import Options\n\nclass Manifest(Options):\n    ..."
        ),
        fix=(
            "Inherit the project base named in the finding and drop "
            "configuration that only repeated it. When a class deliberately "
            "needs the outside base, keep a reasoned suppression or move it "
            "into a package whose contract sets bases: {}. No automatic "
            "rewrite is performed. Enable it with a package contract, e.g. "
            "structure.contracts.<name>: {match: app, bases: "
            "{pydantic.BaseModel: app.core.Options}}."
        ),
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "bases")
            if contract is None or not contract.spec.bases:
                continue
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            mapping = {
                outside: project
                for outside, project in contract.spec.bases.items()
                if not _defines(syntax.tree, source.module, project)
            }
            for owner in ast.walk(syntax.tree):
                if not isinstance(owner, ast.ClassDef):
                    continue
                for base in owner.bases:
                    outside = _resolve(base, syntax.bindings)
                    if outside not in mapping:
                        continue
                    project = mapping[outside]
                    yield self.violation(
                        f"Class {owner.name} inherits {outside} directly; "
                        f"{contract.name} expects {project}",
                        path=source.path,
                        line=owner.lineno,
                        column=owner.col_offset + 1,
                        source_module=source.module,
                        expected={
                            "contract": contract.name,
                            "class": owner.name,
                            "outside_base": outside,
                            "project_base": project,
                            "automatic_rewrite": False,
                        },
                        hint=(
                            f"Inherit {project.rpartition('.')[2]} from "
                            f"{project.rpartition('.')[0]} instead of {outside}."
                        ),
                    )


def _defines(tree: ast.Module, module: str, project: str) -> bool:
    # The project base may be re-exported, so its package prefix is enough.
    package, _, name = project.rpartition(".")
    return is_within(module, package) and any(
        isinstance(node, ast.ClassDef) and node.name == name for node in tree.body
    )


def _resolve(base: ast.expr, bindings: Mapping[str, str]) -> str | None:
    # Generic bases such as Model[T] name their class before the subscript.
    if isinstance(base, ast.Subscript):
        base = base.value
    return qualified_name(base, bindings)
