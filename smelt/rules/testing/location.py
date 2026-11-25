from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from smelt.analysis.files import TestFile


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


def mirror_path(ctx: AnalysisContext, module: str) -> str | None:
    """The mirrored test path of ``module`` under the test root of its package.

    Segments may be placeholders such as ``<layer>``; they are kept as written.
    """
    root = module.split(".", 1)[0]
    for test_root in ctx.config.project.test_roots:
        if root in _member_roots(ctx, test_root.strip("/")):
            return _mirror_target(ctx, test_root.strip("/"), module)
    return None


def _mirrored_modules(ctx: AnalysisContext, path: str) -> list[str]:
    """The modules a discovered test's path mirrors, one per candidate root package.

    ``tests/billing/test_invoice.py`` mirrors ``app.billing.invoice`` whether or
    not that module exists. ``conftest.py`` and paths outside tests.mirror mirror
    nothing.
    """
    test = ctx.files.tests.get(path)
    if test is None or test.is_conftest:
        return []
    mirror = _mirror_parts(ctx, test)
    if mirror is None:
        return []
    return [
        ".".join([root, *mirror.directories, mirror.module])
        for root in _roots(ctx, mirror, test.test_root)
    ]


def _mirror_parts(ctx: AnalysisContext, test: TestFile) -> _Mirror | None:
    relative = test.path.removeprefix(f"{test.test_root}/")
    match = _mirror_regex(ctx.config.tests.mirror).fullmatch(relative)
    if match is None:
        return None
    path = match.group("path") if "path" in match.groupdict() else None
    root = match.group("root") if "root" in match.groupdict() else None
    return _Mirror(path.split("/") if path else [], match.group("module"), root)


def _common_depth(ctx: AnalysisContext, root: str, test_root: str) -> int:
    package = ctx.files.packages.get(root)
    if package is None:
        return 0
    depth = 0
    for left, right in zip(
        package.source_root.strip("/").split("/"), test_root.split("/"), strict=False
    ):
        if left != right or left in ("", "."):
            break
        depth += 1
    return depth


def _roots(ctx: AnalysisContext, mirror: _Mirror, test_root: str) -> list[str]:
    roots = _member_roots(ctx, test_root)
    if mirror.root is None:
        return roots
    return [mirror.root] if mirror.root in roots else []


def _member_roots(ctx: AnalysisContext, test_root: str) -> list[str]:
    """The root packages a test root belongs to.

    In a workspace, ``libs/agent/tests`` tests the package under ``libs/agent/src``:
    the source roots sharing the longest leading path with the test root win.
    """
    roots = ctx.config.project.root_packages
    shared = {root: _common_depth(ctx, root, test_root) for root in roots}
    best = max(shared.values(), default=0)
    if best == 0:
        return roots
    return [root for root in roots if shared[root] == best]


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


class MisplacedTestFile(Rule):
    scope = "test"
    activation = POLICIES["TST001"]
    code = "TST001"
    name = "tests/misplaced-test"
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
            "Move or rename the file so its path mirrors the module it tests, merge it "
            "into that module's test, or list deliberately mirror_exclude tests "
            "(behaviour, integration, e2e) in tests.mirror_exclude. A missing mirrored "
            "source says nothing about whether the tested behaviour exists. Imports "
            "through package facades count as imports of the defining module. A move "
            "is suggested only when the test imports its subject (or is named exactly "
            "after it, marked `evidence: name`); otherwise `expected.candidates` lists "
            "the imported modules with their names and mirrored test paths to choose "
            "from, and two imported modules of the test's name stay undecided."
        ),
        canonical="Put test files where tests.layout expects them",
        config=(
            "tests.layout",
            "tests.mirror",
            "tests.mirror_exclude",
            "tests.mirror_suffixes",
            "project.test_roots",
        ),
    )
