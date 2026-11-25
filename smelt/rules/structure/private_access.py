from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class ForeignPrivateAccess(Rule):
    activation = POLICIES["VIS005"]
    code = "VIS005"
    name = "visibility/foreign-private-access"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary=(
            "Production code uses a private symbol or private attribute owned by "
            "another project module."
        ),
        rationale=(
            "A leading underscore marks a name as private to its owner. Reaching "
            "into a collaborator's private helpers, fields or methods couples the "
            "caller to implementation details. This opt-in review reports two "
            "kinds of access. symbol: relative and absolute from-imports and "
            "module-qualified reads of a module-level private name, through "
            "lexical import aliases, when it statically resolves to a definition "
            "in another analyzed production module, including sibling modules of "
            "the same package. Public aliases remain public, and a package "
            "initializer may re-export its own submodule's private symbol under a "
            "public alias. TYPE_CHECKING follows architecture.import_graph.type_checking. "
            "attribute: reads, writes, calls, updates and deletes of a private "
            "member, such as self.log._images, whose receiver and defining project "
            "class are statically known and live in another module, or another "
            "package under style.private_attribute_boundary: package. Direct "
            "accesses on the method receiver to its own or inherited members are "
            "allowed. Receiver evidence comes from parameter/variable annotations, "
            "direct constructor assignments, stable local aliases, class/instance "
            "fields, property return annotations and bounded single inheritance; "
            "import aliases, relative imports and static facade re-exports "
            "resolve to the defining class. Unlike Ruff SLF001, unresolved "
            "receivers are not reported. Dunders, third-party and unknown names, "
            "private module names, reflection, same-module access, unknown "
            "factories, inherited lookup across multiple/unknown bases, arbitrary "
            "class decorators, local imports in attribute evidence, "
            "comprehension/lambda receiver bodies, closure/global-variable flow, "
            "rebound or shadowed bindings and wildcard imports are skipped. Tests "
            "are covered by tests/private-access (TST002). No control-flow "
            "narrowing, runtime dispatch or analyzed code execution is used."
        ),
        bad=(
            "# app/client.py\nfrom app.session import SessionLog\n"
            "from .service import _normalize\n"
            "def persist(log: SessionLog):\n"
            "    return _normalize(log._images.retain())"
        ),
        good=(
            "# app/client.py\nfrom app.session import SessionLog\n"
            "from .service import normalize\n"
            "def persist(log: SessionLog):\n"
            "    return normalize(log.retain_images())"
        ),
        fix=(
            "Give a shared helper a module-public name, keeping it out of __all__ "
            "and the package facade when it is internal to the package, or move it "
            "into the module that uses it. For attributes, expose a meaningful "
            "operation on the owning class or pass a deliberate public dependency; "
            "do not merely rename or wrap internal state to silence the report. "
            "Preserve evaluation order, exceptions and side effects. No automatic "
            "rewrite is performed. Enable rules: {visibility/foreign-private-access: "
            "warning}, select VIS005, or set private_attribute_boundary under "
            "style or in a style override. The boundary defaults to module; "
            "package permits attribute cooperation between modules of the same "
            "immediate Python package and does not relax private symbols. "
            "Reasoned suppressions on the imported name, read or attribute are "
            "supported."
        ),
        canonical=(
            "Use only your own module's private names; expose a public "
            "operation instead"
        ),
        config=(
            "rules",
            "style.private_attribute_boundary",
            "style.overrides",
            "architecture.import_graph.type_checking",
        ),
    )
