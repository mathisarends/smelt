from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
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


_PLACEHOLDER = re.compile(r"(\{path\}/|\{path\}|\{module\}|\{root\})")
_GROUPS = {
    "{path}/": r"(?:(?P<path>[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)/)?",
    "{path}": r"(?P<path>[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)?",
    "{module}": r"(?P<module>[A-Za-z_]\w*)",
    "{root}": r"(?P<root>[A-Za-z_]\w*)",
}


@cache
def _mirror_regex(pattern: str) -> re.Pattern[str]:
    parts = _PLACEHOLDER.split(pattern)
    return re.compile("".join(_GROUPS.get(part, re.escape(part)) for part in parts))


@dataclass(frozen=True, slots=True)
class _Mirror:
    """What a test path says it mirrors: ``billing/test_invoice.py`` -> billing, invoice."""

    directories: list[str]
    module: str
    root: str | None


def _mirror_parts(ctx: AnalysisContext, test: TestFile) -> _Mirror | None:
    relative = test.path.removeprefix(f"{test.test_root}/")
    match = _mirror_regex(ctx.config.tests.mirror).fullmatch(relative)
    if match is None:
        return None
    path = match.group("path") if "path" in match.groupdict() else None
    root = match.group("root") if "root" in match.groupdict() else None
    return _Mirror(path.split("/") if path else [], match.group("module"), root)


def _roots(ctx: AnalysisContext, mirror: _Mirror) -> list[str]:
    roots = ctx.config.project.root_packages
    if mirror.root is None:
        return roots
    return [mirror.root] if mirror.root in roots else []


def _subjects(subject: str, *, suffixes: bool) -> list[str]:
    """The module names a test may cover: ``invoice_rounding`` -> also ``invoice``."""
    if not suffixes:
        return [subject]
    parts = subject.split("_")
    return ["_".join(parts[:end]) for end in range(len(parts), 0, -1)]


def _mirrors_source(ctx: AnalysisContext, mirror: _Mirror) -> bool:
    files = ctx.files
    for root in _roots(ctx, mirror):
        package = ".".join([root, *mirror.directories])
        package_name = mirror.directories[-1] if mirror.directories else root
        for name in _subjects(mirror.module, suffixes=ctx.config.tests.mirror_suffixes):
            source = files.sources.get(f"{package}.{name}")
            if source is not None and not source.is_package:
                return True
            if name == package_name and package in files.packages:
                return True
    return False


def _missing_source(ctx: AnalysisContext, mirror: _Mirror) -> str:
    root = (_roots(ctx, mirror) or ctx.config.project.root_packages)[0]
    return ctx.files.module_to_path(
        ".".join([root, *mirror.directories, mirror.module])
    )


def _mirror_target(ctx: AnalysisContext, test_root: str, module: str) -> str:
    """Where tests.mirror puts the test of ``module`` (a module, not a package)."""
    root, *directories, name = module.split(".")
    path = "/".join(directories)
    relative = (
        ctx.config.tests.mirror.replace("{path}/", f"{path}/" if path else "")
        .replace("{path}", path)
        .replace("{module}", name)
        .replace("{root}", root)
    )
    return f"{test_root}/{relative}"


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
            "tests/billing/test_billing.py needs app/billing/. Not every module needs a "
            "test. tests.mirror sets the convention, relative to the test root."
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
            "tests.mirror",
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
        mirror = _mirror_parts(ctx, test)
        if mirror is not None and _mirrors_source(ctx, mirror):
            return
        syntax = ctx.syntax.for_path(test.path)
        modules = _imported_modules(ctx, syntax) if syntax is not None else []
        subject = mirror.module if mirror else _subject(test.name)
        expected = self._mirror_path(ctx, test, subject, modules)
        if expected is not None and expected != test.path:
            yield self._misplaced(test, expected)
            return
        hint = (
            "Move or rename the test so its path mirrors the module it tests, "
            "delete it if that module is gone, or list it in tests.unmirrored."
        )
        if mirror is None:
            pattern = ctx.config.tests.mirror
            yield self.violation(
                f"{test.name} does not match tests.mirror ({pattern})",
                path=test.path,
                expected={"pattern": f"{test.test_root}/{pattern}"},
                hint=hint,
            )
            return
        yield self.violation(
            f"{test.name} mirrors no source module: "
            f"{_missing_source(ctx, mirror)} does not exist",
            path=test.path,
            hint=hint,
        )

    def _misplaced(self, test: TestFile, expected: str) -> Violation:
        directory = posixpath.dirname(expected)
        if directory == posixpath.dirname(test.path):
            message = f"{test.name} should be named {posixpath.basename(expected)}"
        else:
            message = f"{test.name} belongs in {directory}/"
        return self.violation(
            message,
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

    def _mirror_path(
        self, ctx: AnalysisContext, test: TestFile, subject: str, modules: list[str]
    ) -> str | None:
        candidates = [
            m
            for m in modules
            if m.rsplit(".", 1)[-1] == subject and m not in ctx.files.packages
        ]
        if len(candidates) != 1:
            return None
        return _mirror_target(ctx, test.test_root, candidates[0])
