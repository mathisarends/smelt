from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category
from smelt.rules.base import Rule, RuleDoc


class PrivateTestAccess(Rule):
    scope = "test"
    code = "TST002"
    name = "tests/private-access"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A test imports or reads a private production name through a known module or instance.",
        rationale="Testing an observable public contract lets internal helpers change without rewriting tests. This opt-in convention checks imports, module-qualified accesses and direct instance reads/calls into analyzed production modules. Instance provenance comes from known production constructors (including analyzed facade re-exports), explicit annotations, and fixture returns/yields with an annotation or consistent known constructor/alias shape. Fixed tuple fixtures preserve known component types when unpacked. Unknown factories, conflicting outputs, reflection, local fakes and dunder names are excluded. Instance assignments are excluded so intentional immutability probes remain valid. A deliberate public re-export remains public. No general type inference or fixture execution is performed.",
        bad="from app.service import _normalize\ndef test_normalize():\n    assert _normalize('A') == 'a'",
        good="from app.service import normalize\ndef test_normalize():\n    assert normalize('A') == 'a'",
        fix="Exercise the supported behavior that uses the helper. Keep a reasoned exception for a deliberate implementation/protocol test; do not expose internals solely to silence a warning. In multiline imports, place the exception on the affected imported name so formatting preserves its scope.",
        canonical=(
            "Test public behavior; do not import or read private production names"
        ),
        config=("rules", "tests.quality_exclude"),
    )
