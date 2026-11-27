from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class PreferCallableInstance(Rule):
    code = "IDM003"
    name = "idioms/prefer-callable-instance"
    category = Category.IDIOMS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review __call__ and an operation-based class name for a single public method.",
        rationale=(
            "A class with one public instance method can express its operation through __call__ "
            "and a class name describing that operation. This opt-in design review reports "
            "one candidate per plain class with exactly one directly declared public method. "
            "Private helpers and __init__ do not count as public operations. An existing __call__ "
            "is already a callable API and is never reported. Constructor and operation signatures "
            "are reported separately, including positional-only, keyword-only and variadic arguments: "
            "construction configures the object, while __call__ must retain the method's call-time arguments "
            "and async behavior. Classes with bases, metaclass keywords, class or method decorators, "
            "additional special methods, class-level data or conditional definitions are excluded. "
            "This keeps inherited APIs, descriptors, dataclasses and explicit protocols outside this "
            "bounded syntax review. Test files are excluded. A single method does not prove a design "
            "defect: published named APIs and stateful lifecycles can justify keeping the class as it is. "
            "Dynamic monkey-patching and external API consumers are not resolved."
        ),
        bad=(
            "class ReportService:\n"
            "    def __init__(self, storage):\n"
            "        self._storage = storage\n\n"
            "    def generate(self, report_id, *, refresh=False):\n"
            "        return self._storage.load(report_id, refresh=refresh)"
        ),
        good=(
            "class GenerateReport:\n"
            "    def __init__(self, storage):\n"
            "        self._storage = storage\n\n"
            "    def __call__(self, report_id, *, refresh=False):\n"
            "        return self._storage.load(report_id, refresh=refresh)"
        ),
        fix=(
            "Consider renaming the sole public method to __call__ and naming the class after its "
            "operation (for example, ReportService.generate becomes GenerateReport.__call__). "
            "Preserve the constructor signature, call-time parameters, defaults, return annotations "
            "and async behavior; do not move constructor dependencies into __call__. Review callers, "
            "dependency injection registrations and public compatibility before changing either name. "
            "No names, arguments or callers are rewritten automatically. Enable rules: "
            "{idioms/prefer-callable-instance: hint}, globally or in a style override, or --select IDM003. "
            "Keep an intentional named API with a package rules override set to off or a reasoned IDM003 suppression."
        ),
        canonical=(
            "Prefer an operation-named class with __call__ over a class with "
            "a single public method"
        ),
        config=("rules", "style.overrides"),
        smell="A plain class has exactly one public method, so its name and the method name describe the same operation twice.",
        no_smell="A published named API or a class with a stateful lifecycle; keep it with a reasoned suppression or a package override set to off.",
        repair="Rename the method to __call__ and the class after its operation; keep constructor and call-time arguments apart and update callers and registrations.",
        subject="{class}.{method}",
    )
