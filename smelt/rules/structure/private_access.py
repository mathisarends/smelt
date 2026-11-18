from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.exports import resolve_dotted, resolve_export
from smelt.analysis.module_access import module_attributes
from smelt.analysis.parsing import resolve_relative, type_checking_lines
from smelt.analysis.private_attributes import PrivateAttributeIndex
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile
    from smelt.analysis.syntax import ModuleSyntax


class ForeignPrivateAccess(Rule):
    activation = POLICIES["SMT339"]
    code = "SMT339"
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
            "are covered by tests/private-access (SMT420). No control-flow "
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
            "warning}, select SMT339, or set private_attribute_boundary under "
            "structure or in a package contract. The boundary defaults to module; "
            "package permits attribute cooperation between modules of the same "
            "immediate Python package and does not relax private symbols. "
            "Reasoned suppressions on the imported name, read or attribute are "
            "supported."
        ),
        config=(
            "rules",
            "style.private_attribute_boundary",
            "style.overrides",
            "architecture.import_graph.type_checking",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        index = PrivateAttributeIndex(ctx.syntax, ctx.files)
        for source in ctx.files.sources.values():
            module = ctx.syntax.for_path(source.path)
            if module is None:
                continue
            yield from self._symbols(ctx, source, module)
            yield from self._attributes(ctx, source, module, index=index)

    def _symbols(
        self, ctx: AnalysisContext, source: SourceFile, module: ModuleSyntax
    ) -> Iterator[Violation]:
        ignored = (
            type_checking_lines(module.tree)
            if ctx.config.architecture.import_graph.type_checking == "ignore"
            else frozenset()
        )
        reexports = set(_module_imports(module.tree)) if source.is_package else set()
        accesses: list[tuple[ast.alias | ast.Attribute, str, str, str]] = []
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.ImportFrom) or node.lineno in ignored:
                continue
            base = resolve_relative(
                source.module,
                is_package=source.is_package,
                level=node.level,
                target=node.module,
            )
            for alias in node.names:
                if (
                    source.is_package
                    and node in reexports
                    and base != source.module
                    and is_within(base, source.module)
                    and alias.asname
                    and not alias.asname.startswith("_")
                ):
                    continue
                accesses.append((alias, base, alias.name, "import"))
        for attribute, qualified in module_attributes(module, ignored):
            resolved = resolve_dotted(qualified, ctx.syntax.namespace)
            if resolved is not None and resolved[1] is None:
                accesses.append((attribute, resolved[0], attribute.attr, "read"))
        for location, base, name, access in accesses:
            if not _private(name):
                continue
            target = resolve_export(base, name, ctx.syntax.namespace)
            if target is None or target[1] is None or target[0] == source.module:
                continue
            qualified = f"{base}.{name}"
            yield self.violation(
                f"production code accesses private module symbol {qualified}",
                path=source.path,
                line=location.lineno,
                column=location.col_offset + 1,
                end_line=location.end_lineno,
                end_column=(location.end_col_offset or location.col_offset) + 1,
                source_module=source.module,
                target_module=base,
                expected={
                    "kind": "symbol",
                    "private_target": qualified,
                    "defined_in": f"{target[0]}.{target[1]}",
                    "access": access,
                    "automatic_rewrite": False,
                },
                hint="Give the shared helper a module-public name without adding it to the package facade, or move it into the caller's module.",
            )

    def _attributes(
        self,
        ctx: AnalysisContext,
        source: SourceFile,
        module: ModuleSyntax,
        *,
        index: PrivateAttributeIndex,
    ) -> Iterator[Violation]:
        boundary = POLICY_DEFAULTS.private_attribute_boundary
        contract = ctx.contracts.policy_for(source.module, "private_attribute_boundary")
        if (
            contract is not None
            and contract.spec.private_attribute_boundary is not None
        ):
            boundary = contract.spec.private_attribute_boundary
        for access in index.accesses(module):
            if access.owner_module == source.module:
                continue
            owner_source = ctx.files.sources[access.owner_module]
            if boundary == "package" and _package(source.module, source.is_package) == (
                _package(access.owner_module, owner_source.is_package)
            ):
                continue
            target = f"{access.owner_class}.{access.node.attr}"
            yield self.violation(
                f"{source.module} performs a private {access.operation} on {target}, owned by {access.owner_module}; expose an operation on its owner",
                path=source.path,
                line=access.node.lineno,
                column=access.node.col_offset + 1,
                end_line=access.node.end_lineno,
                end_column=(access.node.end_col_offset or 0) + 1,
                source_module=source.module,
                target_module=target,
                expected={
                    "kind": "attribute",
                    "boundary": boundary,
                    "owner_module": access.owner_module,
                    "owner_class": access.owner_class,
                    "receiver_type": access.receiver_type,
                    "attribute": access.node.attr,
                    "operation": access.operation,
                    "automatic_rewrite": False,
                },
                hint="Use a deliberate public operation on the collaborator; keep justified cooperation with a narrow suppression or package policy.",
            )


def _package(module: str, is_package: bool) -> str:
    return module if is_package else module.rpartition(".")[0]


def _private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _module_imports(node: ast.AST) -> Iterator[ast.ImportFrom]:
    if isinstance(node, ast.ImportFrom):
        yield node
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        for child in ast.iter_child_nodes(node):
            yield from _module_imports(child)
