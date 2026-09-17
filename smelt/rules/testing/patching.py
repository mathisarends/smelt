import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.code.common import node_violation
from smelt.rules.testing.common import categories, is_private, patches_in

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax

# Underscore names that are public API (namedtuple) or mock internals.
_PUBLIC_UNDERSCORE = frozenset(
    {"_asdict", "_replace", "_fields", "_make", "_field_defaults"}
)


class PatchesInternal(BaseRule):
    code = "SMT402"
    name = "patches-internal"
    category = Category.TESTS
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A test patches a target in a forbidden category.",
        rationale=(
            "Patching first-party internals couples the test to how the code is built, "
            "not what it does: every refactoring breaks it while real bugs slip through. "
            "Patch boundaries (environment, clock, network) instead."
        ),
        bad='@patch("gateway.features.voice.application.session.compute_duration")',
        good=(
            "def test_start(clock: FakeClock) -> None:\n"
            "    session = StartSession(clock=clock)(...)"
        ),
        fix=(
            "Inject the collaborator through a port and pass a fake, or patch the "
            "external boundary it calls."
        ),
        config=("tests.patching.allow", "tests.patching.forbid"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        forbid = set(ctx.config.tests.patching.forbid)
        if not forbid:
            return
        for syntax in ctx.syntax.tests():
            for patch in patches_in(syntax, syntax.tree):
                found = [c for c in categories(ctx, patch) if c in forbid]
                if not found:
                    continue
                shown = patch.target or patch.attribute or "an unresolved target"
                yield node_violation(
                    self,
                    ctx,
                    syntax,
                    patch.node,
                    f"patches {shown} ({', '.join(found)})",
                    expected={"forbid": sorted(forbid)},
                    hint=(
                        "Pass a fake through a port instead of patching internals, or "
                        "patch the external boundary."
                    ),
                )


class PrivateAccess(BaseRule):
    code = "SMT403"
    name = "private-access"
    category = Category.TESTS
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A test reads, writes or imports private names of first-party code.",
        rationale=(
            "Private attributes are free to change. Tests that reach into them fail on "
            "harmless refactorings and verify wiring instead of behavior."
        ),
        bad="service._repository = FakeRepository()",
        good="service = VoiceService(repository=FakeRepository())",
        fix="Go through the public API, or make the dependency a constructor parameter.",
        config=("tests.private_access",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if ctx.config.tests.private_access != "forbid":
            return
        roots = set(ctx.config.project.root_packages)
        for syntax in ctx.syntax.tests():
            for node in syntax.walk():
                if isinstance(node, ast.ImportFrom):
                    yield from self._private_imports(ctx, syntax, node, roots)
                elif isinstance(node, ast.Attribute) and self._is_private_access(
                    syntax, node, roots
                ):
                    yield node_violation(
                        self,
                        ctx,
                        syntax,
                        node,
                        f"accesses private attribute {node.attr}",
                        hint="Use the public API or inject the collaborator.",
                    )

    def _private_imports(
        self,
        ctx: AnalysisContext,
        syntax: ModuleSyntax,
        node: ast.ImportFrom,
        roots: set[str],
    ) -> Iterator[Violation]:
        for alias in node.names:
            qualified = syntax.bindings.get(alias.asname or alias.name, "")
            if qualified.split(".", 1)[0] not in roots or not is_private(alias.name):
                continue
            module = qualified.rsplit(".", 1)[0]
            yield node_violation(
                self,
                ctx,
                syntax,
                node,
                f"imports private name {alias.name} from {module}",
                target_module=module,
                hint="Test through the public API of the module.",
            )

    def _is_private_access(
        self, syntax: ModuleSyntax, node: ast.Attribute, roots: set[str]
    ) -> bool:
        attr = node.attr
        if (
            not is_private(attr)
            or attr in _PUBLIC_UNDERSCORE
            or attr.startswith("_mock")
        ):
            return False
        if isinstance(node.value, ast.Name) and node.value.id in ("self", "cls"):
            return False
        resolved = syntax.resolve(node.value)
        return resolved is None or resolved.split(".", 1)[0] in roots
