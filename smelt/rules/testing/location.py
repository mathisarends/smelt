from __future__ import annotations

import posixpath
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, FileMove, Fix, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.testing.common import first_party_module

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import TestFile
    from smelt.analysis.syntax import ModuleSyntax


def _imported_modules(ctx: AnalysisContext, syntax: ModuleSyntax) -> list[str]:
    found: list[str] = []
    for target in syntax.bindings.values():
        module = first_party_module(ctx, target)
        if module is not None and module not in found:
            found.append(module)
    return found


def _subject(name: str) -> str:
    stem = name.removesuffix(".py")
    return stem.removeprefix("test_").removesuffix("_test")


class MisplacedTestFile(BaseRule):
    code = "SMT401"
    name = "test-location"
    category = Category.TESTS
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES, Index.SYNTAX})
    fixable = True
    doc = RuleDoc(
        summary="A test file does not live where tests.layout expects it.",
        rationale=(
            "A predictable test location lets readers find the tests of a feature and "
            "keeps feature-scoped test runs (`pytest tests/voice`) complete."
        ),
        bad="tests/test_voice_sessions.py      # layout: feature",
        good="tests/voice/test_sessions.py",
        fix="Move the file to the expected path (`smelt fix SMT401`).",
        config=("tests.layout", "tests.pattern", "project.test_roots"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        layout = ctx.config.tests.layout
        if layout == "none":
            return
        for path, test in sorted(ctx.files.tests.items()):
            name = posixpath.basename(path)
            if test.is_conftest or name == "__init__.py":
                continue
            syntax = ctx.syntax.for_path(path)
            if syntax is None:
                continue
            modules = _imported_modules(ctx, syntax)
            if layout == "feature":
                expected = self._feature_path(ctx, path, modules)
            else:
                expected = self._mirror_path(test, modules)
            if expected is None or expected == path:
                continue
            fix = None
            if expected not in ctx.files.tests and not (ctx.root / expected).exists():
                fix = Fix(f"move {path} to {expected}", (FileMove(path, expected),))
            yield self.violation(
                f"{name} belongs in {posixpath.dirname(expected)}/",
                path=path,
                expected={"path": expected},
                hint=f"Move the file to {expected}.",
                fix=fix,
            )

    def _feature_path(
        self, ctx: AnalysisContext, path: str, modules: list[str]
    ) -> str | None:
        features: Counter[str] = Counter()
        for module in modules:
            info = ctx.model.info(module)
            if info is not None and info.feature is not None:
                features[info.feature] += 1
        if not features:
            return None
        pattern = ctx.config.tests.pattern.strip("/")
        for feature in features:
            directory = pattern.replace("{feature}", feature)
            if path.startswith(f"{directory}/"):
                return path
        best = max(sorted(features), key=lambda feature: features[feature])
        directory = pattern.replace("{feature}", best)
        return f"{directory}/{posixpath.basename(path)}"

    def _mirror_path(self, test: TestFile, modules: list[str]) -> str | None:
        subject = _subject(posixpath.basename(test.path))
        candidates = [m for m in modules if m.rsplit(".", 1)[-1] == subject]
        if len(candidates) != 1:
            return None
        directories = candidates[0].split(".")[1:-1]
        return posixpath.join(
            test.test_root, *directories, posixpath.basename(test.path)
        )
