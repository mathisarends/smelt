from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config.errors import ConfigIssue, did_you_mean
from smelt.config.patterns import module_matches

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext


def missing_paths(ctx: AnalysisContext) -> list[ConfigIssue]:
    """Config entries that point at nothing, which would silently switch rules off."""
    issues: list[ConfigIssue] = []
    project = ctx.config.project
    for index, directory in enumerate(project.source_roots):
        if not (ctx.root / directory).is_dir():
            issues.append(
                ConfigIssue(
                    f"project.source_roots[{index}]",
                    f'directory "{directory}" does not exist',
                )
            )
    if ctx.config.tests.layout == "mirror":
        for index, directory in enumerate(project.test_roots):
            if not (ctx.root / directory).is_dir():
                issues.append(
                    ConfigIssue(
                        f"project.test_roots[{index}]",
                        f'directory "{directory}" does not exist',
                    )
                )
    for index, package in enumerate(project.root_packages):
        if package in ctx.files.missing_root_packages:
            roots = ", ".join(project.source_roots)
            issues.append(
                ConfigIssue(
                    f"project.root_packages[{index}]",
                    f'package "{package}" is in none of the source roots ({roots})',
                )
            )
    issues.extend(_missing_modules(ctx))
    return issues


def _missing_modules(ctx: AnalysisContext) -> list[ConfigIssue]:
    arch = ctx.config.architecture
    files = ctx.files
    packages = sorted(files.packages)
    known = {*files.sources, *files.packages}
    issues: list[ConfigIssue] = []
    features = arch.features
    if features is not None:
        key = "root" if features.root is not None else "pattern"
        package = features.root or (features.pattern or "").rsplit(".", 1)[0]
        if package not in files.packages:
            issues.append(
                ConfigIssue(
                    f"architecture.features.{key}",
                    f'package "{package}" does not exist{did_you_mean(package, packages)}',
                )
            )
    for key in ("shared", "composition_root"):
        for index, module in enumerate(getattr(arch, key)):
            if module not in known:
                issues.append(
                    ConfigIssue(
                        f"architecture.{key}[{index}]",
                        f'module "{module}" does not exist{did_you_mean(module, known)}',
                    )
                )
    for index, pattern in enumerate(arch.wiring):
        if not any(module_matches(pattern, module) for module in known):
            issues.append(
                ConfigIssue(
                    f"architecture.wiring[{index}]",
                    f'"{pattern}" matches no module',
                )
            )
    return issues
