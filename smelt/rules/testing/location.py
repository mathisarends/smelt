from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


def mirror_path(ctx: AnalysisContext, module: str) -> str | None:
    """The mirrored test path of ``module`` under the test root of its package.

    Segments may be placeholders such as ``<layer>``; they are kept as written.
    """
    root = module.split(".", 1)[0]
    for test_root in ctx.config.project.test_roots:
        if root in _member_roots(ctx, test_root.strip("/")):
            return _mirror_target(ctx, test_root.strip("/"), module)
    return None


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
