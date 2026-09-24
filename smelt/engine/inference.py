from __future__ import annotations

import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
# distribution name -> import name
_DI_FRAMEWORKS = {
    "dishka": "dishka",
    "dependency-injector": "dependency_injector",
    "injector": "injector",
    "lagom": "lagom",
    "punq": "punq",
    "wireup": "wireup",
    "svcs": "svcs",
    "kink": "kink",
    "that-depends": "that_depends",
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
    di_frameworks: list[str] = field(default_factory=list)
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
    inferred.di_frameworks = sorted(
        {
            module
            for location in [root, *member_roots]
            for module in _di_frameworks(location)
        }
    )
    if inferred.has_features and inferred.features:
        tests_dir = root / inferred.test_roots[0]
        if any((tests_dir / feature).is_dir() for feature in inferred.features):
            inferred.tests_layout = "feature"
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


def _di_frameworks(root: Path) -> list[str]:
    pyproject = root / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project", {})
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    names = {
        re.split(r"[\s\[<>=!~;@]", requirement, maxsplit=1)[0].lower().replace("_", "-")
        for requirement in requirements
        if isinstance(requirement, str)
    }
    return sorted(module for dist, module in _DI_FRAMEWORKS.items() if dist in names)


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
            "# Roles give concepts a canonical home (`smelt context <feature>` lists them).",
            "# roles:",
            "#   port:",
            "#     detect: { base: typing.Protocol }",
            "#     layers: [application]",
            "#     file: ports.py",
            "#   adapter:",
            "#     detect: { implements: port }",
            "#     layers: [infrastructure]",
            "",
            "structure:",
            "  forbidden_names: [utils, helpers]",
            "",
            "tests:",
            f"  layout: {inferred.tests_layout}  # mirror | feature | none",
        ]
    )
    if inferred.tests_layout == "feature":
        out.append(f'  pattern: "{inferred.test_roots[0]}/{{feature}}"')
    out.extend(
        [
            "",
            "# Severity overrides by code: error | warning | hint | off",
            "# rules:",
            "#   SMT304: off",
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
    if inferred.di_frameworks:
        out.append(
            f"  di_frameworks: {_list(inferred.di_frameworks)}  # composition root and wiring only"
        )
    out.append("")
    if inferred.layers:
        out.append("  layers:")
        for layer in inferred.layers:
            out.extend(
                [
                    f"    {layer.name}:",
                    f"      path: {layer.path}",
                    f"      may_depend_on: {_list(layer.may_depend_on)}",
                ]
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
        out.extend(["  cross_feature:", "    default: deny", f"    allow: {pair}"])
    return out


def _wiring_lines(modules: list[str]) -> list[str]:
    if not modules:
        return []
    return [
        "  wiring:  # exact provider modules; keep their original feature/layer",
        *(f"    - {module}" for module in modules),
    ]


def _list(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"
