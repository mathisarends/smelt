from __future__ import annotations

import ast
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.config.patterns import module_matches
from smelt.engine.mirror_inference import DEFAULT_MIRROR, MirrorGuess, infer_mirror

if TYPE_CHECKING:
    from pathlib import Path

_IGNORED_DIRS = frozenset(
    {
        "tests",
        "test",
        "docs",
        "doc",
        "scripts",
        "examples",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "migrations",
        "venv",
        "env",
    }
)
_FEATURE_CONTAINERS = (
    "features",
    "modules",
    "domains",
    "contexts",
    "bounded_contexts",
    "components",
    "apps",
)
_SHARED_NAMES = ("shared", "common", "kernel", "shared_kernel")
# root-level modules every layer may read: settings, logging setup
_SHARED_MODULES = ("settings", "config", "env", "logging_config", "_logging")
# central packages next to the features, by the layer they usually belong to
_CENTRAL_NAMES: dict[str, tuple[str, ...]] = {
    "infrastructure": (
        "platform",
        "infrastructure",
        "infra",
        "adapters",
        "persistence",
        "database",
        "db",
        "storage",
    ),
    "presentation": ("presentation", "api", "web", "http", "interfaces"),
    "application": ("application", "services", "use_cases"),
    "domain": ("domain",),
}
_COMPOSITION_NAMES = (
    "bootstrap",
    "composition_root",
    "container",
    "containers",
    "di",
    "wiring",
    "main",
    "__main__",
    "lifespan",
)
# layer -> directory names, most conventional first
LAYER_ALIASES: dict[str, tuple[str, ...]] = {
    "domain": ("domain", "entities"),
    "application": ("application", "use_cases", "usecases", "services"),
    "infrastructure": ("infrastructure", "infra", "adapters", "persistence"),
    "presentation": ("presentation", "api", "web", "http", "interfaces", "ui"),
}
_LAYER_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "domain": (),
    "application": ("domain",),
    "infrastructure": ("domain", "application"),
    "presentation": ("application", "domain"),
}


@dataclass
class InferredLayer:
    name: str
    path: str
    may_depend_on: list[str]


@dataclass
class InferredConfig:
    root_packages: list[str]
    source_roots: list[str]
    test_roots: list[str]
    features_root: str | None = None
    features_pattern: str | None = None
    features: list[str] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)
    composition_root: list[str] = field(default_factory=list)
    wiring: list[str] = field(default_factory=list)
    modules: dict[str, str] = field(default_factory=dict)
    unclassified_roots: list[str] = field(default_factory=list)
    mirror: MirrorGuess | None = None
    layers: list[InferredLayer] = field(default_factory=list)
    tests_layout: str = "none"

    @property
    def has_features(self) -> bool:
        return self.features_root is not None or self.features_pattern is not None


def _child_packages(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (child for child in directory.iterdir() if _has_python(child)),
        key=lambda p: p.name,
    )


def _has_python(directory: Path) -> bool:
    return (
        directory.is_dir()
        and directory.name.isidentifier()
        and directory.name not in _IGNORED_DIRS
        and any(directory.rglob("*.py"))
    )


def infer_config(root: Path) -> InferredConfig | None:
    source_root = (
        "src" if (root / "src").is_dir() and _packages_in(root / "src") else "."
    )
    base = root / source_root
    packages = _packages_in(base)
    source_roots = [source_root] if packages else []
    member_roots: list[Path] = []
    for member in _workspace_members(root):
        member_source = member / "src" if _packages_in(member / "src") else member
        found = _packages_in(member_source)
        if not found:
            continue
        packages.extend(found)
        source_roots.append(member_source.relative_to(root).as_posix())
        member_roots.append(member)
    if not packages:
        return None
    test_roots = [name for name in ("tests", "test") if (root / name).is_dir()]
    test_roots.extend(
        (member / name).relative_to(root).as_posix()
        for member in member_roots
        for name in ("tests", "test")
        if (member / name).is_dir()
    )
    inferred = InferredConfig(
        root_packages=[p.name for p in packages],
        source_roots=source_roots,
        test_roots=test_roots or ["tests"],
    )
    _infer_features(inferred, packages)
    containers = _layer_containers(inferred, packages)
    _infer_layers(inferred, containers)
    if inferred.has_features and not inferred.layers:
        inferred.features_root = inferred.features_pattern = None
        inferred.features = []
    _infer_special_modules(inferred, packages)
    _infer_wiring(inferred, packages)
    _infer_app_factories(inferred, packages)
    _infer_central_modules(inferred, packages)
    inferred.wiring = _wiring_patterns(inferred.wiring, _all_modules(packages))
    inferred.mirror = infer_mirror(root, inferred.test_roots, packages)
    if inferred.mirror is not None:
        inferred.tests_layout = "mirror"
    return inferred


def _workspace_members(root: Path) -> list[Path]:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    uv = data.get("tool", {}).get("uv", {})
    workspace = uv.get("workspace", {})
    members = workspace.get("members", [])
    if not isinstance(members, list):
        return []
    found: set[Path] = set()
    for pattern in members:
        if not isinstance(pattern, str):
            continue
        for candidate in root.glob(pattern):
            if candidate.is_dir() and candidate.resolve().is_relative_to(
                root.resolve()
            ):
                found.add(candidate)
    return sorted(found)


def _packages_in(base: Path) -> list[Path]:
    return [child for child in _child_packages(base) if any(child.glob("*.py"))]


def _infer_features(inferred: InferredConfig, packages: list[Path]) -> None:
    for package in packages:
        for depth_one in [package, *_child_packages(package)]:
            for name in _FEATURE_CONTAINERS:
                candidate = depth_one / name
                children = _child_packages(candidate)
                if children:
                    dotted = ".".join(candidate.relative_to(package.parent).parts)
                    inferred.features_root = dotted
                    inferred.features = [c.name for c in children]
                    return
    layer_names = {alias for aliases in LAYER_ALIASES.values() for alias in aliases}
    for package in packages:
        layered = [
            child
            for child in _child_packages(package)
            if child.name not in _SHARED_NAMES
            and len({c.name for c in _child_packages(child)} & layer_names) >= 2  # noqa: PLR2004
        ]
        if len(layered) >= 2:  # noqa: PLR2004
            inferred.features_pattern = f"{package.name}.{{feature}}"
            inferred.features = [child.name for child in layered]
            return


def _layer_containers(inferred: InferredConfig, packages: list[Path]) -> list[Path]:
    by_name = {p.name: p for p in packages}
    if inferred.features_root is not None:
        first, *rest = inferred.features_root.split(".")
        container = by_name[first].joinpath(*rest)
        return _child_packages(container)
    if inferred.features_pattern is not None:
        root_name = inferred.features_pattern.split(".")[0]
        return [by_name[root_name] / name for name in inferred.features]
    return packages


def _infer_layers(inferred: InferredConfig, containers: list[Path]) -> None:
    counts: Counter[str] = Counter(
        child.name for container in containers for child in _child_packages(container)
    )
    found: dict[str, str] = {}
    for name, aliases in LAYER_ALIASES.items():
        count, _, alias = max(
            (counts[alias], -index, alias) for index, alias in enumerate(aliases)
        )
        if count > 0:
            found[name] = alias
    inferred.layers = [
        InferredLayer(name, path, _dependencies(name, found))
        for name, path in found.items()
    ]


def _dependencies(layer: str, found: dict[str, str]) -> list[str]:
    if layer == "presentation" and "application" not in found:
        return ["domain"] if "domain" in found else []
    return [dep for dep in _LAYER_DEPENDENCIES[layer] if dep in found]


def _infer_special_modules(inferred: InferredConfig, packages: list[Path]) -> None:
    for package in packages:
        for name in _SHARED_NAMES:
            if _has_python(package / name):
                inferred.shared.append(f"{package.name}.{name}")
        for name in _COMPOSITION_NAMES:
            if (package / f"{name}.py").is_file() or _has_python(package / name):
                inferred.composition_root.append(f"{package.name}.{name}")
        for name in _SHARED_MODULES:
            if (package / f"{name}.py").is_file():
                inferred.shared.append(f"{package.name}.{name}")


def _infer_app_factories(inferred: InferredConfig, packages: list[Path]) -> None:
    """Root-level modules between the entry point and the wiring, like ``app.py``.

    A module counts when a composition root imports it and it imports a composition
    root or wiring module itself: it assembles the app and is part of the root.
    Package facades count as the wiring they re-export: ``app.py`` importing
    ``backend.features``, whose ``__init__`` collects the feature providers.
    """
    roots = list(inferred.composition_root)
    targets = [*roots, *inferred.wiring]
    for package in packages:
        for path in sorted(package.glob("*.py")):
            module = f"{package.name}.{path.stem}"
            if path.stem == "__init__" or module in roots or module in inferred.shared:
                continue
            imports = _imports(path, package.name)
            if not any(_reaches(packages, name, targets) for name in imports):
                continue
            if any(module in _module_imports(packages, root) for root in roots):
                inferred.composition_root.append(module)


def _infer_central_modules(inferred: InferredConfig, packages: list[Path]) -> None:
    """Map packages beside the features to a layer by their name."""
    if not inferred.has_features:
        return
    layers = {layer.name for layer in inferred.layers}
    container = (
        inferred.features_root or (inferred.features_pattern or "").rsplit(".", 1)[0]
    )
    taken = [*inferred.shared, *inferred.composition_root]
    for package in packages:
        if not _within_any(container, [package.name]):
            inferred.unclassified_roots.append(package.name)
            continue
        for child in _child_packages(package):
            module = f"{package.name}.{child.name}"
            if _within_any(container, [module]) or _within_any(module, taken):
                continue
            if inferred.features_pattern and child.name in inferred.features:
                continue
            layer = next(
                (
                    name
                    for name, aliases in _CENTRAL_NAMES.items()
                    if child.name in aliases and name in layers
                ),
                None,
            )
            if layer is not None:
                inferred.modules[module] = layer


def _wiring_patterns(modules: list[str], known: set[str]) -> list[str]:
    """Collapse ``a.x.di`` and ``a.y.di`` into ``a.*.di`` when it matches only wiring."""
    remaining = sorted(modules)
    patterns: list[str] = []
    while remaining:
        parts = remaining[0].split(".")
        best: tuple[str, list[str]] | None = None
        for index in range(1, len(parts)):
            pattern = ".".join([*parts[:index], "*", *parts[index + 1 :]])
            group = [m for m in remaining if module_matches(pattern, m)]
            exact = all(m in modules for m in known if module_matches(pattern, m))
            if len(group) > 1 and exact and (best is None or len(group) > len(best[1])):
                best = (pattern, group)
        if best is None:
            patterns.append(remaining.pop(0))
            continue
        patterns.append(best[0])
        remaining = [m for m in remaining if m not in best[1]]
    return sorted(patterns)


def _all_modules(packages: list[Path]) -> set[str]:
    found: set[str] = set()
    for package in packages:
        for path in package.rglob("*.py"):
            parts = path.relative_to(package.parent).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            found.add(".".join(parts))
    return found


def _module_file(packages: list[Path], module: str) -> Path | None:
    first, *rest = module.split(".")
    for package in packages:
        if package.name == first:
            candidate = package.joinpath(*rest)
            if candidate.with_suffix(".py").is_file():
                return candidate.with_suffix(".py")
            if (candidate / "__init__.py").is_file():
                return candidate / "__init__.py"
    return None


def _reaches(
    packages: list[Path], name: str, targets: list[str], depth: int = 3
) -> bool:
    """``name`` is one of ``targets`` or a package facade re-exporting one."""
    if _within_any(name, targets):
        return True
    path = _module_file(packages, name)
    if depth == 0 or path is None or path.name != "__init__.py":
        return False
    return any(
        _reaches(packages, imported, targets, depth - 1)
        for imported in _module_imports(packages, name)
        if _within_any(imported, [name])
    )


def _module_imports(packages: list[Path], module: str) -> set[str]:
    """Absolute names ``module`` imports; relative ones resolved from its package."""
    path = _module_file(packages, module)
    if path is None:
        return set()
    parent = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    return _imports(path, parent)


def _imports(path: Path | None, package: str) -> set[str]:
    """Absolute names a module directly inside ``package`` imports."""
    if path is None:
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.rsplit(".", node.level - 1)[0]
                base = f"{parent}.{base}" if base else parent
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _within_any(module: str, packages: list[str]) -> bool:
    return any(
        module == package or module.startswith(f"{package}.") for package in packages
    )


def _infer_wiring(inferred: InferredConfig, packages: list[Path]) -> None:
    for package in packages:
        for path in package.rglob("*.py"):
            relative = path.relative_to(package.parent)
            if any(part in _IGNORED_DIRS for part in relative.parts):
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if "from dishka import" not in content or not re.search(
                r"class\s+\w+\s*\(\s*Provider\b", content
            ):
                continue
            inferred.wiring.append(".".join(relative.with_suffix("").parts))
    inferred.wiring.sort()


def render_config(inferred: InferredConfig) -> str:
    out = [
        "# Architecture guardrails for smelt. Reference: `smelt config schema`,",
        "# rule details: `smelt explain <code>`.",
        "version: 1",
        "",
        "project:",
        f"  root_packages: {_list(inferred.root_packages)}",
        f"  source_roots: {_list(inferred.source_roots)}",
        f"  test_roots: {_list(inferred.test_roots)}",
        '  # exclude: ["**/migrations/**"]',
        "",
        "architecture:",
    ]
    out.extend(_architecture(inferred))
    out.extend(
        [
            "",
            "structure:",
            "  forbidden_names: [utils, helpers]",
            "",
            "tests:",
            f"  layout: {inferred.tests_layout}  # mirror | none",
        ]
    )
    if inferred.mirror is not None and inferred.mirror.pattern != DEFAULT_MIRROR:
        out.append(f'  mirror: "{inferred.mirror.pattern}"')
    out.extend(
        [
            "",
            "# Severity overrides by code: error | warning | hint | off",
            "# rules:",
            "#   SMT305: off",
            "",
            "# Adopt incrementally: `smelt debt` records today's violations.",
            "# debt: .smelt/debt.json",
        ]
    )
    return "\n".join(out) + "\n"


def _architecture(inferred: InferredConfig) -> list[str]:
    out: list[str] = []
    if inferred.features_root is not None:
        out.append("  features:")
        out.append(
            f"    root: {inferred.features_root}  # every direct child package is a feature"
        )
    elif inferred.features_pattern is not None:
        out.append("  features:")
        out.append(f'    pattern: "{inferred.features_pattern}"')
    else:
        out.append("  # features:")
        out.append(
            "  #   root: myapp.features  # every direct child package is a feature"
        )
    if inferred.shared:
        out.append(
            f"  shared: {_list(inferred.shared)}  # importable everywhere; must not import features"
        )
    else:
        out.append("  # shared: [myapp.shared]")
    if inferred.composition_root:
        out.append(f"  composition_root: {_list(inferred.composition_root)}")
    else:
        out.append("  # composition_root: [myapp.bootstrap]")
    out.extend(_wiring_lines(inferred.wiring))
    out.extend(_module_lines(inferred))
    out.append("")
    if inferred.layers:
        out.append("  layers:")
        for layer in inferred.layers:
            out.extend(
                [
                    f"    {layer.name}:",
                    f"      path: {layer.path}",
                    f"      may_depend_on: {_list(layer.may_depend_on)}",
                    "      third_party: allow  # review which frameworks this layer may use",
                ]
            )
            if layer.name in ("domain", "application"):
                out.append(
                    "      # For a framework-free core: third_party: {default: deny, allow: [pydantic]}"
                )
    else:
        out.extend(
            [
                "  # layers:",
                "  #   domain:",
                "  #     path: domain",
                "  #     may_depend_on: []",
                "  #   application:",
                "  #     path: application",
                "  #     may_depend_on: [domain]",
            ]
        )
    if inferred.has_features:
        pair = (
            '["application -> application"]'
            if any(layer.name == "application" for layer in inferred.layers)
            else "[]"
        )
        out.extend(
            [
                "  cross_feature:",
                "    default: deny",
                "    # Review this architectural decision: the pair applies to ALL features.",
                f"    allow: {pair}",
            ]
        )
    out.extend(
        [
            "  imports:",
            "    transitive: false  # direct imports only; true also checks layer dependencies through re-exports",
            "    type_checking: include",
        ]
    )
    return out


def _wiring_lines(modules: list[str]) -> list[str]:
    if not modules:
        return []
    return [
        "  wiring:  # provider modules; they keep their feature and layer",
        *(f"    - {module}" for module in modules),
    ]


def _module_lines(inferred: InferredConfig) -> list[str]:
    if not inferred.has_features:
        return []
    if not inferred.modules and not inferred.unclassified_roots:
        return []
    header = "modules:  # packages outside the features and their layer"
    out = [f"  {header}" if inferred.modules else f"  # {header}"]
    out.extend(f"    {module}: {layer}" for module, layer in inferred.modules.items())
    out.extend(
        f"    # {package}: infrastructure  # pick the layer that may use it"
        for package in inferred.unclassified_roots
    )
    return out


def _list(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"
