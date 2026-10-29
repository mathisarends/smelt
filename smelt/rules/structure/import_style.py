from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import resolve_relative, type_checking_lines
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile
    from smelt.config.models import ImportStyleConfig


class ImportStyle(BaseRule):
    activation = PolicyActivation(
        "A package contract declares an imports style.",
        lambda config: any(
            contract.imports is not None
            for contract in config.structure.contracts.values()
        ),
    )
    code = "SMT308"
    name = "import-style"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An import does not follow the package's declared relative/absolute style.",
        rationale=(
            "A predictable import style makes ownership easier to read. With relative "
            "style and max_relative_level: 1, local imports use one dot; parent and "
            "cross-root imports stay absolute. Deeper relative imports can be explicitly "
            "allowed. The closest package contract supplies the style. No style is "
            "enforced without an explicit contract, and this rule never changes bindings."
        ),
        bad="# app/storage/service.py, relative style with maximum 1\nfrom app.storage.models import Record\nfrom ..domain import Entity",
        good="# app/storage/service.py\nfrom .models import Record\nfrom app.domain import Entity",
        fix=(
            "Use a relative import within the configured dot limit, otherwise use "
            "an absolute import. Preserve aliases and update references if converting "
            "an import statement changes its binding. There is no automatic rewrite."
        ),
        config=("structure.contracts", "architecture.imports.type_checking"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.style_for(source.module)
            if contract is None or contract.spec.imports is None:
                continue
            tree = ctx.asts.parse(source.path)
            guarded = type_checking_lines(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Import | ast.ImportFrom):
                    continue
                if (
                    node.lineno in guarded
                    and ctx.config.architecture.imports.type_checking == "ignore"
                ):
                    continue
                yield from self._statement(
                    ctx, source, node, contract.spec.imports, contract.name
                )

    def _statement(
        self,
        ctx: AnalysisContext,
        source: SourceFile,
        node: ast.Import | ast.ImportFrom,
        policy: ImportStyleConfig,
        contract: str,
    ) -> Iterator[Violation]:
        package = (
            source.module if source.is_package else source.module.rpartition(".")[0]
        )
        if isinstance(node, ast.ImportFrom):
            targets = [
                resolve_relative(
                    source.module,
                    is_package=source.is_package,
                    level=node.level,
                    target=node.module,
                )
            ]
            current_level = node.level
        else:
            # Relative imports bind a child of a package. Importing the package
            # object itself must be compared against its parent, not itself.
            targets = [alias.name for alias in node.names]
            current_level = 0
        for target in targets:
            if not ctx.model.is_first_party(target):
                continue
            base = (
                target
                if isinstance(node, ast.ImportFrom)
                else target.rpartition(".")[0]
            )
            relative = _relative_base(package, base)
            wants_relative = (
                policy.style == "relative"
                and relative is not None
                and relative[0] <= policy.max_relative_level
            )
            if wants_relative and 0 < current_level <= policy.max_relative_level:
                continue
            if not wants_relative and current_level == 0:
                continue
            expected_style = "relative" if wants_relative else "absolute"
            hint = (
                f"Use from {relative[1]} import ...; preserve names and aliases."
                if wants_relative and relative is not None
                else f"Use an absolute import from {target}."
            )
            yield self.violation(
                f'contract "{contract}" expects an {expected_style} import of {target}',
                path=source.path,
                line=node.lineno,
                column=node.col_offset + 1,
                end_line=node.end_lineno,
                end_column=(node.end_col_offset or node.col_offset) + 1,
                source_module=source.module,
                target_module=target,
                expected={
                    "contract": contract,
                    "style": expected_style,
                    "max_relative_level": policy.max_relative_level,
                    "relative_base": relative[1]
                    if wants_relative and relative
                    else None,
                },
                hint=hint,
            )


def _relative_base(package: str, target: str) -> tuple[int, str] | None:
    source_parts, target_parts = package.split("."), target.split(".") if target else []
    common = 0
    for source_part, target_part in zip(source_parts, target_parts, strict=False):
        if source_part != target_part:
            break
        common += 1
    if common == 0:
        return None
    level = len(source_parts) - common + 1
    return level, "." * level + ".".join(target_parts[common:])
