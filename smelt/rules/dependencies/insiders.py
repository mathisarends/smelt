from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.model import is_within
from smelt.rules.testing.location import mirrored_modules

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.contracts import ResolvedContract


def inside_contract(
    ctx: AnalysisContext, path: str, importer: str, *, contract: ResolvedContract
) -> bool:
    """Whether the import in ``path`` comes from inside the contracted package.

    Production code inside the package is. Tests are outside callers, unless the
    contract sets ``test_access: mirrored`` and the test mirrors one of its modules.
    """
    if not ctx.files.is_test_path(path):
        return is_within(importer, contract.package)
    return contract.spec.test_access == "mirrored" and any(
        is_within(module, contract.package) for module in mirrored_modules(ctx, path)
    )
