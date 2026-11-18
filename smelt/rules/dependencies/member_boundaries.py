from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.patterns import path_matches
from smelt.diagnostics.violation import Category, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.common import import_violation, skip_import

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportDetail


class MemberBoundary(Rule):
    activation = POLICIES["SMT111"]
    code = "SMT111"
    name = "architecture/member-boundary"
    category = Category.ARCHITECTURE
    requires = frozenset({Index.IMPORTS, Index.WORKSPACE})
    doc = RuleDoc(
        summary="A workspace member imports a member outside its may_depend_on allowlist.",
        rationale=(
            "Member-local layers describe internal design; workspace member boundaries "
            "describe which projects may depend on one another. Only direct production "
            "imports between discovered members count. Unmatched source members are "
            "unrestricted, overlapping source globs combine their allowances, and imports "
            "inside one member always pass. Wiring and shared modules retain these boundaries. "
            "TYPE_CHECKING follows architecture.import_graph.type_checking; transitive does not apply."
        ),
        bad=(
            "# packages/core/src/core/model.py\nfrom api.http import serve\n"
            "# root policy: architecture.members: {'packages/*': {may_depend_on: []}}"
        ),
        good=(
            "# services/api/src/api/http.py\nfrom core.model import Order\n"
            "# root policy: architecture.members: {'services/*': {may_depend_on: ['packages/*']}}"
        ),
        fix="Move the dependency to an allowed member or invert it behind an interface; change the root allowlist only if the dependency belongs in the intended design.",
        config=(
            "project.workspace",
            "architecture.members",
            "architecture.import_graph.type_checking",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for detail, source, target in _member_imports(ctx):
            if (allowed := _boundary_restriction(ctx, source, target)) is not None:
                yield import_violation(
                    self,
                    ctx,
                    detail,
                    message=f"member {source} must not depend on {target}",
                    expected={
                        "source_member": source,
                        "target_member": target,
                        "may_depend_on": allowed,
                    },
                    hint="Move this dependency behind an allowed member boundary; the workspace root declares architecture.members.",
                    edge=f"{source} -> {target}",
                )


class UndeclaredMemberDependency(Rule):
    activation = POLICIES["SMT112"]
    code = "SMT112"
    name = "architecture/undeclared-member-dependency"
    category = Category.ARCHITECTURE
    requires = frozenset({Index.IMPORTS, Index.WORKSPACE})
    doc = RuleDoc(
        summary="A workspace member imports another member without declaring a direct project dependency.",
        rationale=(
            "One shared import graph makes undeclared imports work locally, but the member "
            "may fail when installed independently. This rule compares direct production "
            "imports with project.dependencies, project.optional-dependencies and tool.uv.sources. Distribution names are "
            "normalized independently of Python package names. Markers and extras count "
            "without evaluating the analyzer's environment. uv sources alone do not declare "
            "dependencies. A member source overrides the root source, even when marked. "
            "Dependency groups and test imports are not assessed; projects with dynamic "
            "dependencies or without static metadata are skipped. "
            "architecture.import_graph.transitive does not require transitive declarations."
        ),
        bad='[project]\nname = "api"\ndependencies = []\n# api imports core.model',
        good='[project]\nname = "api"\ndependencies = ["core-lib"]\n[tool.uv.sources]\ncore-lib = {workspace = true}',
        example_language="toml",
        fix=(
            "For an allowed dependency, declare the imported member's distribution "
            "in project.dependencies and configure its uv workspace source. "
            "If architecture.members forbids the dependency, remove the import "
            "or move the dependency behind an allowed member boundary."
        ),
        config=(
            "project.workspace",
            "architecture.import_graph.type_checking",
            "architecture.members",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        declared = ctx.workspace.dependencies
        for detail, source, target in _member_imports(ctx):
            target_project = ctx.workspace.metadata.get(target, {}).get("project", {})
            if (
                source not in declared
                or not isinstance(target_project, dict)
                or not isinstance(target_project.get("name"), str)
                or target in declared[source]
            ):
                continue
            name = target_project["name"]
            hint = (
                "Remove this import or move the dependency behind an allowed member boundary. "
                "The root architecture.members policy forbids this dependency (SMT111)."
                if _boundary_restriction(ctx, source, target) is not None
                else f"Declare {name} in {source}/pyproject.toml project.dependencies and configure its tool.uv.sources workspace source."
            )
            yield import_violation(
                self,
                ctx,
                detail,
                message=f"member {source} imports {target} without a declared dependency on {name}",
                expected={
                    "source_member": source,
                    "target_member": target,
                    "dependency": name,
                    "declared_members": sorted(declared[source]),
                },
                hint=hint,
                edge=f"{source} -> {target}",
            )


def _boundary_restriction(
    ctx: AnalysisContext, source: str, target: str
) -> list[str] | None:
    """Return the applicable allowlist when it rejects the dependency."""
    policies = [
        policy
        for pattern, policy in ctx.config.architecture.members.items()
        if path_matches(pattern, source)
    ]
    if not policies:
        return None
    allowed = sorted(
        {pattern for policy in policies for pattern in policy.may_depend_on}
    )
    return (
        None if any(path_matches(pattern, target) for pattern in allowed) else allowed
    )


def _member_imports(ctx: AnalysisContext) -> Iterator[tuple[ImportDetail, str, str]]:
    for detail in ctx.imports.all_imports():
        if detail.external or skip_import(ctx, detail):
            continue
        source = ctx.workspace.member_for_module(detail.importer)
        target = ctx.workspace.member_for_module(detail.imported)
        if source is not None and target is not None and source != target:
            yield detail, source, target
