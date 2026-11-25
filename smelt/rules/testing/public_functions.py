from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class TestOnlyPublicFunction(Rule):
    code = "VIS009"
    name = "visibility/test-only-public-function"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS, Index.TESTS})
    doc = RuleDoc(
        summary="A public module function is accessed from tests but has no observed production use outside its own module.",
        rationale=(
            "Leaving an internal helper public just so tests can call it enlarges the apparent API and couples tests to implementation details. This opt-in review identifies undecorated top-level sync/async functions without a leading underscore, referenced by analyzed tests and not imported or accessed by another analyzed production module. Uses inside the defining module do not justify public visibility. Aliased/relative imports and analyzed re-exports are resolved; production imports, star imports and escaped module values conservatively preserve the API. Explicit __all__ exports, dynamic export declarations, decorated functions, rebound names, methods and nested functions are excluded. Test-quality exclusions apply to test evidence. External consumers, packaging entry points and arbitrary dynamic access are not fully known; a finding is a review candidate, not proof that a function can be made private safely."
            " Excluded files (including maintainer scripts), packages outside source selection and external consumers are unassessed, not proven absent. Findings expose the configured usage_scope."
            " When no references exist inside the defining module either, the finding says unused outside tests among analyzed modules and lists the internal reference evidence. This is not proof of dead code for unassessed consumers."
        ),
        bad="__all__ = ['drag']\nasync def drag_gesture(tab): ...\nasync def drag(tab):\n    await drag_gesture(tab)\n# tests/test_mouse.py\nfrom app.mouse import drag_gesture",
        good="__all__ = ['drag']\nasync def _drag_gesture(tab): ...\nasync def drag(tab):\n    await _drag_gesture(tab)\n# tests/test_mouse.py\nfrom app.mouse import drag",
        fix="If this is an internal helper, add a leading underscore and update internal references. Prefer testing the public behavior that calls it. If direct helper tests are deliberate, keep a reasoned private-access exception; if it is a supported external API, declare it in __all__ or document a reasoned suppression. Review callers before renaming.",
        canonical=(
            "Do not make functions public only for tests; test them through "
            "public behavior"
        ),
        config=("rules", "tests.quality_exclude"),
    )
