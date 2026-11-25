from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class DuplicateNarrowingAssert(Rule):
    code = "IDM007"
    name = "idioms/duplicate-narrowing-assert"
    category = Category.IDIOMS
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Several methods of a class assert the same narrowing of a self attribute.",
        rationale=(
            "assert self._stack is not None in one method narrows a type for "
            "the checker. When several methods of the class repeat it, the "
            "attribute is optional in its type but required after setup, and "
            "every caller depends on a check that python -O removes. This "
            "opt-in review reports, once per class and expression, an assert "
            "of self.<attribute> is not None or isinstance(self.<attribute>, "
            "...) that appears in at least two methods of the same class, "
            "including each operand of an and. Asserts in nested functions "
            "belong to that function, static methods are skipped and tests "
            "are not checked. Unlike a blanket assert ban, single local "
            "narrowings stay valid."
        ),
        bad=(
            "class Tabs:\n"
            "    def open(self, url: str) -> None:\n"
            "        assert self._stack is not None\n"
            "        self._stack.push(url)\n\n"
            "    def close(self) -> None:\n"
            "        assert self._stack is not None\n"
            "        self._stack.pop()"
        ),
        good=(
            "class Tabs:\n"
            "    @property\n"
            "    def stack(self) -> Stack:\n"
            "        if self._stack is None:\n"
            '            raise RuntimeError("Tabs used before start()")\n'
            "        return self._stack\n\n"
            "    def open(self, url: str) -> None:\n"
            "        self.stack.push(url)"
        ),
        fix=(
            "Make the attribute required by constructing it before the object "
            "is used, or read it through one property or accessor that raises "
            "an error naming the missing setup step. No automatic rewrite is "
            "performed. Enable rules: {idioms/duplicate-narrowing-assert: warning}, or "
            "select IDM007."
        ),
        canonical=(
            "Set required attributes at construction instead of asserting "
            "them in every method"
        ),
        config=("rules",),
    )
