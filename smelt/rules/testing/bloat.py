import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.testing.common import is_mock_factory, patch_call

if TYPE_CHECKING:
    from collections.abc import Iterator


class BloatedTestChange(BaseRule):
    code = "SMT407"
    name = "test-bloat"
    category = Category.TESTS
    default_severity = Severity.HINT
    requires = frozenset({Index.FILES, Index.SYNTAX, Index.CHANGES})
    doc = RuleDoc(
        summary="A change adds far more test code than production code.",
        rationale=(
            "Test code is code to maintain. Hundreds of test lines for a small change "
            "usually mean copy-pasted setup or tests of implementation details."
        ),
        bad="+420 test lines for a 12-line change",
        good="one focused, parametrized test per behavior",
        fix="Parametrize similar cases, share setup in fixtures and drop redundant tests.",
        config=("tests.bloat.ratio", "tests.bloat.min_test_loc"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        changes = ctx.changes
        if changes is None:
            return
        bloat = ctx.config.tests.bloat
        test_roots = [root.strip("/") for root in ctx.config.project.test_roots]
        test_added: dict[str, int] = {}
        production = 0
        for path, change in changes.files.items():
            if not path.endswith(".py"):
                continue
            if path in ctx.files.tests or any(
                path.startswith(f"{r}/") for r in test_roots
            ):
                test_added[path] = change.added
            elif path in ctx.files.by_path:
                production += change.added + change.deleted
        added = sum(test_added.values())
        if added < bloat.min_test_loc or production == 0:
            return
        ratio = added / production
        if ratio <= bloat.ratio:
            return
        mocks, patches, assertions = _test_summary(ctx, list(test_added))
        largest = max(sorted(test_added), key=lambda path: test_added[path])
        yield self.violation(
            f"{added} test lines added for {production} production lines changed "
            f"(ratio {ratio:.1f}, max {bloat.ratio:g}; {mocks} mocks, {patches} patches, "
            f"{assertions} assertions)",
            path=largest,
            expected={"ratio": bloat.ratio, "min_test_loc": bloat.min_test_loc},
            hint="Parametrize similar cases and share setup in fixtures.",
        )


def _test_summary(ctx: AnalysisContext, paths: list[str]) -> tuple[int, int, int]:
    mocks = patches = assertions = 0
    for path in paths:
        syntax = ctx.syntax.for_path(path) if path in ctx.files.tests else None
        if syntax is None:
            continue
        for node in syntax.walk():
            if isinstance(node, ast.Assert):
                assertions += 1
            elif isinstance(node, ast.Call) and patch_call(syntax, node) is not None:
                patches += 1
            elif isinstance(node, ast.Call) and is_mock_factory(node):
                mocks += 1
    return mocks, patches, assertions
