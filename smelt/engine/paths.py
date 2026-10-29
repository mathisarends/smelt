from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config.errors import ConfigIssue, did_you_mean
from smelt.config.models import CrossFeatureAllowance
from smelt.config.patterns import module_matches

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.config.loader import LoadedConfig


def project_metadata(loaded: LoadedConfig) -> dict[str, str]:
    root = loaded.root.resolve()
    repository = next(
        (
            directory
            for directory in (root, *root.parents)
            if (directory / ".git").exists()
        ),
        root,
    )
    prefix = root.relative_to(repository).as_posix()
    return {
        "root": root.as_posix(),
        "config": loaded.path.resolve().as_posix(),
        "repository_root": repository.as_posix(),
        "repository_prefix": "" if prefix == "." else prefix,
    }


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
    issues.extend(_conflicting_package_policies(ctx))
    matched_contracts = {contract.name for contract in ctx.contracts.resolved}
    issues.extend(
        ConfigIssue(
            f"structure.contracts.{name}.match",
            f'"{contract.match}" matches no analyzed package',
        )
        for name, contract in ctx.config.structure.contracts.items()
        if name not in matched_contracts
    )
    for index, allowance in enumerate(ctx.config.architecture.cross_feature.allow):
        if not isinstance(allowance, CrossFeatureAllowance):
            continue
        for key, value in (("from", allowance.source), ("to", allowance.target)):
            feature = value.split(".")[0]
            if feature not in ctx.model.features:
                issues.append(
                    ConfigIssue(
                        f"architecture.cross_feature.allow[{index}].{key}",
                        f'unknown feature "{feature}"{did_you_mean(feature, ctx.model.features)}',
                    )
                )
    return issues


def _conflicting_package_policies(ctx: AnalysisContext) -> list[ConfigIssue]:
    policies: dict[tuple[str, str], object] = {}
    issues: list[ConfigIssue] = []
    for contract in ctx.contracts.resolved:
        for key, label in (
            ("imports", "import styles"),
            ("module_docstrings", "module docstring policies"),
            ("future_annotations", "future annotation policies"),
            ("private_modules", "private module naming policies"),
            ("ordering", "definition ordering policies"),
        ):
            policy = getattr(contract.spec, key)
            if policy is None:
                continue
            previous = policies.setdefault((contract.package, key), policy)
            if previous != policy:
                issues.append(
                    ConfigIssue(
                        f"structure.contracts.{contract.name}.{key}",
                        f"conflicting {label} for {contract.package}; use one policy per package",
                    )
                )
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
    issues.extend(
        ConfigIssue(
            f"architecture.modules.{module}",
            f'module "{module}" does not exist{did_you_mean(module, known)}',
        )
        for module in arch.modules
        if module not in known
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
