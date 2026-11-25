from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class RedundantPrivateDelegate(Rule):
    code = "VIS006"
    name = "visibility/redundant-private-delegate"
    category = Category.VISIBILITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A method only forwards its parameters to a private method used nowhere else.",
        rationale=(
            "def events(self, *, run_id): return self._events(run_id=run_id) adds "
            "a second name and a jump without behavior: calling an async "
            "generator already defers its body until iteration, so moving the "
            "body into events behaves the same. Validation before the call is the "
            "legitimate reason for the split, so only a body consisting of one "
            "return is reported. The private method must be an undecorated method "
            "of the same class with the same parameter names and kinds, receive "
            "every parameter unchanged, and be referenced nowhere else in the "
            "module. Dunder and decorated methods, private names defined twice, "
            "classes with bases from other modules (a base may call the private "
            "hook) and tests are not reported. Subclasses in other modules that "
            "override the private method are not analyzed."
        ),
        bad=(
            "class Frames:\n"
            "    def events(self, *, run_id: UUID) -> AsyncGenerator[Frame]:\n"
            "        return self._events(run_id=run_id)\n\n"
            "    async def _events(self, *, run_id: UUID) -> AsyncGenerator[Frame]:\n"
            "        async for frame in self._source(run_id):\n"
            "            yield frame"
        ),
        good=(
            "class Frames:\n"
            "    async def events(self, *, run_id: UUID) -> AsyncGenerator[Frame]:\n"
            "        async for frame in self._source(run_id):\n"
            "            yield frame"
        ),
        fix=(
            "Move the private method's body and async/def kind into the public "
            "method, keep the public name, signature and docstring, and delete the "
            "private method. Keep the split with a reasoned suppression when a "
            "subclass overrides the private method. No automatic rewrite is "
            "performed. Enable rules: {visibility/redundant-private-delegate: hint}, or "
            "select VIS006."
        ),
        canonical=(
            "Do not wrap a private method in a public method that only forwards to it"
        ),
        config=("rules",),
    )
