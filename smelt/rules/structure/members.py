from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.member_names import private_name

if TYPE_CHECKING:
    from collections.abc import Iterator


class InternalPublicMember(BaseRule):
    activation = PolicyActivation(
        "A package contract sets member_visibility to private-when-internal.",
        lambda config: any(
            contract.member_visibility == "private-when-internal"
            for contract in config.structure.contracts.values()
        ),
    )
    code = "SMT316"
    name = "internal-public-member"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS})
    doc = RuleDoc(
        summary="A public module function or instance method has no observed use outside its module or class.",
        rationale="An implementation helper should not advertise a public interface solely because it lacks a leading underscore. This opt-in package review includes unused definitions and internally used sync/async functions and ordinary instance methods. Imports and module escapes preserve module functions; known test accesses also count as external use, leaving test-only public functions to SMT429. Explicit module __all__ exports and decorated functions are preserved. Methods on decorated, inherited or subclassed classes, special/private methods, properties and class/static methods are excluded. For methods, even unresolved same-named attribute accesses outside the owning class conservatively preserve visibility. External libraries, frameworks, dynamic reflection and unscanned callers cannot be ruled out. This finding asks for review and does not prove a rename is safe.",
        bad="class Browser:\n    def emit(self, event): ...\n    def run(self):\n        self.emit('ready')\nBrowser().run()",
        good="class Browser:\n    def _emit(self, event): ...\n    def run(self):\n        self._emit('ready')\nBrowser().run()",
        fix="Review the supported interface and callers. For a genuine internal helper, add a leading underscore and update calls and callback references. Resolve existing private-name collisions before renaming. Preserve deliberate public APIs with __all__ for module functions or a reasoned suppression for a method. No automatic rename is performed.",
        config=("structure.contracts", "project.test_roots"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not self.activation.matches(ctx.config):
            return
        for member in ctx.member_usage.members:
            contract = ctx.contracts.policy_for(member.module, "member_visibility")
            if (
                contract is None
                or contract.spec.member_visibility != "private-when-internal"
            ):
                continue
            node = member.node
            if (
                node.name.startswith("_")
                or member.kind == "staticmethod"
                or member.external_use
            ):
                continue
            qualified = (
                f"{member.owner.name}.{node.name}" if member.owner else node.name
            )
            suggested_name = private_name(node.name, member.occupied_names)
            yield self.violation(
                f"{qualified} is public, but has no observed use outside its {'class' if member.owner else 'module'}",
                path=member.path,
                line=node.lineno,
                column=node.col_offset + 1,
                source_module=member.module,
                target_module=f"{member.module}.{qualified}",
                expected={
                    "contract": contract.name,
                    "kind": member.kind,
                    "class": member.owner.name if member.owner else None,
                    "member": node.name,
                    "suggested_name": suggested_name,
                    "observed_usage": "internal-only"
                    if member.internal_references
                    else "unused",
                    "internal_references": list(member.internal_references),
                    "automatic_rename": False,
                },
                hint=f"Review making {qualified} private as {suggested_name}; update calls and callbacks, checking for existing private-name collisions.",
            )


class PreferPrivateModuleFunction(BaseRule):
    activation = PolicyActivation(
        "A package contract sets static_methods to module-functions.",
        lambda config: any(
            contract.static_methods == "module-functions"
            for contract in config.structure.contracts.values()
        ),
    )
    code = "SMT317"
    name = "prefer-private-module-function"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.MEMBERS})
    doc = RuleDoc(
        summary="An internal static helper can be reviewed for extraction to a private module function.",
        rationale="A helper that needs neither instance nor class state is often easier to find and reuse as a private module function. This package convention reviews direct sync/async methods carrying only the recognized builtin staticmethod decorator, including import aliases. Known or unresolved external same-named attribute access preserves static APIs. Classes with inheritance, subclasses or decorators, additional method decorators, special methods, __class__/super references and detected class-namespace dependencies are excluded. This is a placement preference, not proof of equivalent runtime behavior; external/dynamic consumers and definition-time dependencies still need review.",
        bad="class Browser:\n    @staticmethod\n    def _normalize(value):\n        return value.strip()\n    def send(self, value):\n        return self._normalize(value)",
        good="def _normalize(value):\n    return value.strip()\n\nclass Browser:\n    def send(self, value):\n        return _normalize(value)",
        fix="Move the helper to module scope with a leading underscore, remove @staticmethod, and update class/instance-qualified calls and callback references. Preserve annotations, defaults, evaluation order and dependencies; choose a distinct name if the module already binds it. Keep deliberate static APIs with a closest allow override or reasoned suppression. No automatic extraction is performed.",
        config=("structure.contracts", "project.test_roots"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not self.activation.matches(ctx.config):
            return
        for member in ctx.member_usage.members:
            contract = ctx.contracts.policy_for(member.module, "static_methods")
            if contract is None or contract.spec.static_methods != "module-functions":
                continue
            if (
                member.kind != "staticmethod"
                or member.external_use
                or member.class_dependencies
                or member.node.name.startswith("__")
            ):
                continue
            node = member.node
            name = private_name(node.name, member.module_names)
            yield self.violation(
                f"{member.owner.name if member.owner else ''}.{node.name} is an internal static helper; prefer a private module function",
                path=member.path,
                line=node.lineno,
                column=node.col_offset + 1,
                source_module=member.module,
                target_module=f"{member.module}.{member.owner.name if member.owner else ''}.{node.name}",
                expected={
                    "contract": contract.name,
                    "class": member.owner.name if member.owner else None,
                    "method": node.name,
                    "suggested_name": name,
                    "destination": member.module,
                    "internal_references": list(member.internal_references),
                    "automatic_extraction": False,
                },
                hint=f"Review moving this helper to {name} at module scope and update qualified calls and callbacks, preserving defaults and annotations.",
            )
