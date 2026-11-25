from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class TestOnlyPublicAttribute(Rule):
    code = "VIS010"
    name = "visibility/test-only-public-attribute"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS, Index.TESTS})
    doc = RuleDoc(
        summary="Tests read a public attribute with no observed production access outside its visibility boundary.",
        rationale=(
            "Exposing implementation state just for tests enlarges the apparent API and couples tests to internal representation. This opt-in review covers direct instance assignments in ordinary methods and simple class data declarations on unambiguous top-level classes. Reads by analyzed tests are resolved through known constructors, annotations, fixtures and class aliases/re-exports. The owning class is the default visibility boundary. With public_attribute_boundary set to module, production code in the defining module counts as internal. Any same-named production attribute access outside the boundary, including unresolved receivers and literal reflection, conservatively preserves visibility. Private names, methods/properties, descriptors, decorated/inherited/subclassed classes, slots and custom attribute hooks are excluded. Test-quality exclusions apply to test evidence; test writes alone do not trigger a finding. External consumers and arbitrary dynamic access remain unknown, so renaming requires review."
            " Excluded files (including maintainer scripts), unselected packages and external consumers are unassessed, not proven absent; usage_scope records the configured analysis boundary."
            " Public attributes of classes explicitly exported through __all__ or public package-facade bindings are treated as supported API and retained, including aliased re-export chains."
        ),
        bad="class Worker:\n    def __init__(self):\n        self.pending = []\n    def run(self):\n        self.pending.clear()\n# tests/test_worker.py\nfrom app.worker import Worker\ndef test_run():\n    worker = Worker()\n    worker.run()\n    assert worker.pending == []",
        good="class Worker:\n    def __init__(self):\n        self._pending = []\n    def run(self):\n        self._pending.clear()\n        return 'done'\n# tests/test_worker.py\nfrom app.worker import Worker\ndef test_run():\n    assert Worker().run() == 'done'",
        fix="Review whether the attribute belongs to the supported API. For internal state, add a leading underscore, update internal references and test observable public behavior. Keep deliberate external or framework contracts with a reasoned suppression. Suggested names avoid observed collisions. Smelt leaves renaming to the caller.",
        canonical=(
            "Do not make attributes public only for tests; test observable behavior"
        ),
        config=(
            "rules",
            "style.public_attribute_boundary",
            "style.overrides",
            "tests.quality_exclude",
            "project.test_roots",
        ),
    )
