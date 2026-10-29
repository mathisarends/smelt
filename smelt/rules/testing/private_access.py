from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


def _private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


class PrivateTestAccess(BaseRule):
    activation = PolicyActivation(
        "tests.forbid_private_imports is enabled.",
        lambda config: config.tests.forbid_private_imports,
    )
    code = "SMT420"
    name = "private-test-access"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A test imports or reads a private production name through a known module or instance.",
        rationale="Testing an observable public contract lets internal helpers change without rewriting tests. This opt-in convention checks imports, module-qualified accesses and direct instance reads/calls into analyzed production modules. Instance provenance comes from known production constructors, explicit annotations and annotated local/conftest fixture returns. Simple aliases are tracked; unknown factories, reflection, local fakes and dunder names are excluded. Instance assignments are excluded so intentional immutability probes remain valid. A deliberate public re-export remains public. No general type inference or fixture execution is performed.",
        bad="from app.service import _normalize\ndef test_normalize():\n    assert _normalize('A') == 'a'",
        good="from app.service import normalize\ndef test_normalize():\n    assert normalize('A') == 'a'",
        fix="Exercise the public behavior that uses the helper, or deliberately expose a supported interface. Use a reasoned suppression for a focused implementation test you intend to keep.",
        config=("tests.forbid_private_imports", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not ctx.config.tests.forbid_private_imports:
            return
        known = set(ctx.files.sources) | set(ctx.files.packages)
        for module in ctx.test_syntax.modules.values():
            seen: set[tuple[int, int, str]] = set()
            accesses = [
                (node, qualified, "import", "module")
                for node, qualified in [*module.imports, *module.attributes]
            ]
            accesses.extend(
                (node, qualified, provenance, "instance")
                for node, qualified, provenance in module.instance_attributes
            )
            for node, qualified, provenance, access in accesses:
                parts = qualified.split(".")
                if not any(
                    ".".join(parts[:end]) in known for end in range(1, len(parts) + 1)
                ):
                    continue
                private = next(
                    (index for index, name in enumerate(parts) if _private(name)), None
                )
                if private is None:
                    continue
                target = ".".join(parts[: private + 1])
                key = (node.lineno, node.col_offset, target)
                if key in seen:
                    continue
                seen.add(key)
                yield self.violation(
                    f"test accesses private production name {target}",
                    path=module.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    target_module=target,
                    expected={
                        "forbid_private_imports": True,
                        "private_target": target,
                        "access": access,
                        "provenance": provenance,
                    },
                    hint="Test the supported public behavior instead of an internal helper or module.",
                )
