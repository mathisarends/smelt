from smelt.analysis.context import Index
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class PreferDeferredAnnotations(Rule):
    code = "TYP006"
    name = "typing/prefer-deferred-annotations"
    category = Category.TYPING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An annotation is quoted where deferred evaluation makes quotes unnecessary.",
        rationale=(
            'Quoted annotations such as list["BrowserWorkspaceTab"] are invisible '
            "to rename refactorings, read differently from their neighbors and "
            "need a decision at every forward reference. One module-level "
            "from __future__ import annotations, or a declared Python 3.14+ "
            "minimum, defers every annotation instead. This opt-in style review "
            "reports one finding per module that quotes annotations of parameters, "
            "returns or annotated assignments: on 3.14+ targets or with the "
            "future import, the quotes are redundant; otherwise the module should "
            "add the future import. Strings inside Literal and Annotated metadata "
            "are values, not annotations, and are ignored, as are quoted names in "
            "runtime expressions such as cast() or TypeVar(bound=...). A package "
            "contract with future_annotations: forbid keeps quotes on targets "
            "before 3.14. Tests are not checked."
        ),
        bad=(
            "class BrowserHandoff(ABC):\n"
            "    @abstractmethod\n"
            '    async def tabs(self) -> list["BrowserWorkspaceTab"]: ...'
        ),
        good=(
            "from __future__ import annotations\n\n"
            "class BrowserHandoff(ABC):\n"
            "    @abstractmethod\n"
            "    async def tabs(self) -> list[BrowserWorkspaceTab]: ..."
        ),
        fix=(
            "Remove the quotes; on targets before Python 3.14 add "
            "from __future__ import annotations first. The future import makes "
            "every annotation in the module a string at runtime, so verify "
            "runtime annotation consumers (dataclasses, Pydantic, FastAPI, "
            "dependency injection) and names imported only under TYPE_CHECKING. "
            "check --fix removes the quotes when the future import already defers "
            "the module's annotations; adding the import, and unquoting on 3.14+ "
            "targets where annotations are evaluated on access, needs "
            "--unsafe-fixes. Quoted text that is not a single-line expression is "
            "left for a manual edit. Enable rules: "
            "{typing/prefer-deferred-annotations: hint}, or select TYP006."
        ),
        canonical="Write annotations without quotes and rely on deferred evaluation",
        config=("rules", "style.overrides", "project.python_version"),
    )
