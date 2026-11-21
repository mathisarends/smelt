from __future__ import annotations

import ast
import fnmatch
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.testing.location import mirror_path

if TYPE_CHECKING:
    from collections.abc import Iterator


def _is_stub(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A body without behaviour: a docstring, ``...``, ``pass`` or a bare raise."""
    for statement in function.body:
        if isinstance(statement, ast.Pass | ast.Raise):
            continue
        if isinstance(statement, ast.Expr) and isinstance(
            statement.value, ast.Constant
        ):
            continue
        return False
    return True


def _has_behaviour(tree: ast.Module) -> bool:
    """Whether the module defines a function or method with a real body."""
    return any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not _is_stub(node)
        for node in ast.walk(tree)
    )


class MissingMirrorTest(Rule):
    scope = "test"
    code = "TST012"
    name = "tests/missing-mirror-test"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A source module with behaviour has no test at its mirrored path.",
        rationale=(
            "With `layout: mirror`, the mirrored path is where readers and agents look "
            "for the tests of a module. TST001 keeps existing tests in place, but a "
            "module added without a test goes unnoticed. This opt-in review reports "
            "modules that define at least one function or method with a real body and "
            "have no test file at the path tests.mirror gives them, or, with "
            "tests.mirror_suffixes, no topic test next to it. Packages (`__init__.py`), "
            "`__main__.py` and modules that only declare data, constants, protocols or "
            "abstract stubs are not reported. A module may still be covered by "
            "behaviour, integration or package tests elsewhere; the rule does not "
            "follow imports or measure coverage."
        ),
        bad="app/billing/invoice.py         # def total(...) ...\ntests/billing/                 # no test_invoice.py",
        good="app/billing/invoice.py\ntests/billing/test_invoice.py",
        fix=(
            "Add the mirrored test file with tests of the module's behaviour. When "
            "another test deliberately covers the module, mark the module with "
            "`# smelt: ignore-file[TST012] -- <reason>`, or turn the rule off for a "
            "package in its `rules`. In an existing project, record current gaps "
            "with `smelt adopt` so changed checks report only new modules. "
            "Enable rules: "
            "{tests/missing-mirror-test: warning} or select TST012."
        ),
        config=(
            "rules",
            "tests.layout",
            "tests.mirror",
            "tests.mirror_suffixes",
            "project.test_roots",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if ctx.config.tests.layout != "mirror":
            return
        tests = ctx.files.tests
        for module, source in sorted(ctx.files.sources.items()):
            if source.is_package or module.rsplit(".", 1)[-1] == "__main__":
                continue
            expected = mirror_path(ctx, module)
            if expected is None or expected in tests:
                continue
            if ctx.config.tests.mirror_suffixes and self._has_topic_test(ctx, module):
                continue
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None or not _has_behaviour(syntax.tree):
                continue
            info = ctx.model.info(module)
            yield self.violation(
                f"{module} has no test at its mirrored path {expected}",
                path=source.path,
                line=1,
                feature=info.feature if info else None,
                layer=info.layer if info else None,
                expected={"test_path": expected},
                hint=(
                    f"Add {expected} with tests of the module's behaviour, or keep a "
                    "reasoned suppression when other tests deliberately cover it."
                ),
            )

    @staticmethod
    def _has_topic_test(ctx: AnalysisContext, module: str) -> bool:
        pattern = mirror_path(ctx, f"{module}_*")
        return pattern is not None and any(
            fnmatch.fnmatchcase(path, pattern) for path in ctx.files.tests
        )
