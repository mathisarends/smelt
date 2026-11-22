from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import resolve_relative
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.edits import SourceText, rewrite_from_module

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.contracts import ResolvedContract
    from smelt.analysis.files import SourceFile
    from smelt.analysis.syntax import ImportStatement
    from smelt.config.models import ImportStyleConfig


class ImportStyle(Rule):
    activation = POLICIES["IMP003"]
    code = "IMP003"
    name = "imports/forbidden-import-style"
    category = Category.IMPORTS
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An import does not follow the package's declared relative/absolute style.",
        rationale=(
            "A predictable import style makes ownership easier to read. With relative "
            "style and max_relative_level: 1, local imports use one dot; parent and "
            "cross-root imports stay absolute. Deeper relative imports can be explicitly "
            "allowed. The closest style override supplies the style, else "
            "style.imports. No style is enforced without one, and this rule "
            "never changes bindings."
        ),
        bad="# app/storage/service.py, relative style with maximum 1\nfrom app.storage.models import Record\nfrom ..domain import Entity",
        good="# app/storage/service.py\nfrom .models import Record\nfrom app.domain import Entity",
        fix=(
            "Use a relative import within the configured dot limit, otherwise use "
            "an absolute import. Preserve aliases and update references if converting "
            "an import statement changes its binding. check --fix rewrites the "
            "module of a from-import, which keeps every binding; a plain import "
            "statement is left for a manual edit."
        ),
        canonical="Follow the package's relative/absolute import style",
        config=(
            "style.imports",
            "style.overrides",
            "architecture.import_graph.type_checking",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.style_for(target.module)
        policy = contract.spec.imports if contract else None
        if policy is None:
            return None
        if policy.style == "absolute":
            return "Use absolute imports for first-party modules"
        dots = (
            "one dot"
            if policy.max_relative_level == 1
            else (f"{policy.max_relative_level} dots")
        )
        return f"Use relative imports up to {dots} inside the package; absolute beyond"

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.style_for(source.module)
            if contract is None or contract.spec.imports is None:
                continue
            for node in ctx.syntax.import_statements(source.path):
                if (
                    node.guarded
                    and ctx.config.architecture.import_graph.type_checking == "ignore"
                ):
                    continue
                yield from self._statement(
                    ctx,
                    source,
                    node,
                    policy=contract.spec.imports,
                    contract=contract,
                )

    def _statement(
        self,
        ctx: AnalysisContext,
        source: SourceFile,
        node: ImportStatement,
        *,
        policy: ImportStyleConfig,
        contract: ResolvedContract,
    ) -> Iterator[Violation]:
        package = (
            source.module if source.is_package else source.module.rpartition(".")[0]
        )
        if node.from_import:
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
            base = target if node.from_import else target.rpartition(".")[0]
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
            spelling = relative[1] if wants_relative and relative else target
            yield self.violation(
                f"{contract.origin} expects {'a' if wants_relative else 'an'} {expected_style} import of {target}",
                path=source.path,
                line=node.lineno,
                column=node.col_offset + 1,
                end_line=node.end_lineno,
                end_column=(node.end_col_offset or node.col_offset) + 1,
                source_module=source.module,
                target_module=target,
                expected={
                    "contract": contract.name,
                    "style": expected_style,
                    "max_relative_level": policy.max_relative_level,
                    "relative_base": relative[1]
                    if wants_relative and relative
                    else None,
                },
                hint=hint,
                fix=_fix(ctx, source, node, spelling=spelling)
                if node.from_import
                else None,
            )


def _fix(
    ctx: AnalysisContext, source: SourceFile, node: ImportStatement, *, spelling: str
) -> Fix | None:
    """Safe: both spellings of a from-import bind the same names to the same objects."""
    text = SourceText(ctx.files.read_text(source.path))
    edit = rewrite_from_module(
        text,
        text.offset(node.lineno, node.col_offset),
        current="." * node.level + (node.module or ""),
        spelling=spelling,
    )
    if edit is None:
        return None
    return Fix(Applicability.SAFE, f"Import from {spelling}", (edit,))


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
