from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class MemberBoundary(Rule):
    activation = POLICIES["ARC009"]
    code = "ARC009"
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
        canonical=(
            "Import other workspace members only as architecture.members allows"
        ),
        config=(
            "project.workspace",
            "architecture.members",
            "architecture.import_graph.type_checking",
        ),
    )


class UndeclaredMemberDependency(Rule):
    activation = POLICIES["ARC010"]
    code = "ARC010"
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
        canonical=(
            "Declare a workspace member as a project dependency before importing it"
        ),
        config=(
            "project.workspace",
            "architecture.import_graph.type_checking",
            "architecture.members",
        ),
    )
