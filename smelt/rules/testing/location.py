from __future__ import annotations

import posixpath
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.patterns import path_matches
from smelt.diagnostics.violation import Category, Severity, Violation
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


def _mirror_parts(test: TestFile) -> tuple[list[str], str]:
    """``tests/billing/test_invoice.py`` -> (["billing"], "invoice")."""
    relative = test.path.removeprefix(f"{test.test_root}/")
    *directories, name = relative.split("/")
    return directories, _subject(name)


def _subjects(subject: str, *, suffixes: bool) -> list[str]:
    """The module names a test may cover: ``invoice_rounding`` -> also ``invoice``."""
    if not suffixes:
        return [subject]
    parts = subject.split("_")
    return ["_".join(parts[:end]) for end in range(len(parts), 0, -1)]


def _mirrors_source(ctx: AnalysisContext, test: TestFile) -> bool:
    directories, subject = _mirror_parts(test)
    files = ctx.files
    for root in ctx.config.project.root_packages:
        package = ".".join([root, *directories])
        package_name = directories[-1] if directories else root
        for name in _subjects(subject, suffixes=ctx.config.tests.mirror_suffixes):
            source = files.sources.get(f"{package}.{name}")
            if source is not None and not source.is_package:
                return True
            if name == package_name and package in files.packages:
                return True
    return False


def _missing_source(ctx: AnalysisContext, test: TestFile) -> str:
    directories, subject = _mirror_parts(test)
    roots = ctx.config.project.root_packages
    return ctx.files.module_to_path(".".join([roots[0], *directories, subject]))


class MisplacedTestFile(BaseRule):
    code = "SMT401"
    name = "test-location"
    category = Category.TESTS
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A test file does not live where tests.layout expects it.",
        rationale=(
            "A predictable test location lets readers and agents find the tests of a "
            "module, and a test whose mirrored source no longer exists is left over from "
            "a rename or move. With `layout: mirror` the path decides: "
            "tests/billing/test_invoice.py needs app/billing/invoice.py, a package test "
            "tests/billing/test_billing.py needs app/billing/. Not every module needs a test."
        ),
        bad="tests/test_invoice.py            # layout: mirror, source app/billing/invoice.py",
        good="tests/billing/test_invoice.py",
        fix=(
            "Move or rename the file so its path mirrors the module it tests, or list "
            "deliberately unmirrored tests (integration, e2e) in tests.unmirrored."
        ),
        config=(
            "tests.layout",
            "tests.pattern",
            "tests.unmirrored",
            "tests.mirror_suffixes",
            "project.test_roots",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        tests = ctx.config.tests
        if tests.layout == "none":
            return
        for path, test in sorted(ctx.files.tests.items()):
            if test.is_conftest:
                continue
            if tests.layout == "feature":
                yield from self._check_feature(ctx, test)
            elif not any(path_matches(p, path) for p in tests.unmirrored):
                yield from self._check_mirror(ctx, test)

    def _check_feature(
        self, ctx: AnalysisContext, test: TestFile
    ) -> Iterator[Violation]:
        syntax = ctx.syntax.for_path(test.path)
        if syntax is None:
            return
        expected = self._feature_path(ctx, test.path, _imported_modules(ctx, syntax))
        if expected is not None and expected != test.path:
            yield self._misplaced(test, expected)

    def _check_mirror(
        self, ctx: AnalysisContext, test: TestFile
    ) -> Iterator[Violation]:
        if _mirrors_source(ctx, test):
            return
        syntax = ctx.syntax.for_path(test.path)
        modules = _imported_modules(ctx, syntax) if syntax is not None else []
        expected = self._mirror_path(test, modules)
        if expected is not None and expected != test.path:
            yield self._misplaced(test, expected)
            return
        missing = _missing_source(ctx, test)
        yield self.violation(
            f"{test.name} mirrors no source module: {missing} does not exist",
            path=test.path,
            hint=(
                "Move or rename the test so its path mirrors the module it tests, "
                "delete it if that module is gone, or list it in tests.unmirrored."
            ),
        )

    def _misplaced(self, test: TestFile, expected: str) -> Violation:
        return self.violation(
            f"{test.name} belongs in {posixpath.dirname(expected)}/",
            path=test.path,
            expected={"path": expected},
            hint=f"Move the file to {expected}.",
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
