from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


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
        canonical="Add a test at the mirrored path for every module with behavior",
        config=(
            "rules",
            "tests.layout",
            "tests.mirror",
            "tests.mirror_suffixes",
            "project.test_roots",
        ),
    )
