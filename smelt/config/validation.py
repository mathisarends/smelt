from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from smelt.config.errors import ConfigIssue, did_you_mean
from smelt.config.models import CrossFeatureAllowance, SmeltConfig
from smelt.config.patterns import module_prefixes
from smelt.config.workspace import architecture_scopes

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from smelt.config.models import (
        ArchitectureConfig,
        LayerBinding,
        LayerConfig,
    )


def partial_layer_issues(raw: dict[str, object]) -> list[ConfigIssue]:
    """Validate references with a complete layer namespace even if other keys failed."""
    sections = [("architecture", raw.get("architecture"))]
    members = raw.get("members")
    if isinstance(members, dict):
        sections.extend(
            (f"members.{path}.architecture", member.get("architecture"))
            for path, member in members.items()
            if isinstance(member, dict)
        )
    issues: list[ConfigIssue] = []
    for location, architecture in sections:
        if not isinstance(architecture, dict) or not isinstance(
            architecture.get("layers"), dict
        ):
            continue
        # An invalid layer declaration makes the namespace unreliable. An invalid
        # cross-feature section still permits checking the layer declarations.
        try:
            layers = SmeltConfig.model_validate(
                {
                    "version": 1,
                    "project": {},
                    "architecture": {"layers": architecture["layers"]},
                }
            )
        except ValidationError:
            continue
        partial = layers
        if "cross_feature" in architecture:
            try:
                partial = SmeltConfig.model_validate(
                    {
                        "version": 1,
                        "project": {},
                        "architecture": {
                            "layers": architecture["layers"],
                            "cross_feature": architecture["cross_feature"],
                        },
                    }
                )
            except ValidationError:
                partial = layers
        validator = _Validator(
            partial, architecture=partial.architecture, roots=(), location=location
        )
        validator.check_layers()
        issues.extend(validator.errors)
    return issues


def validate_semantics(
    config: SmeltConfig,
) -> tuple[list[ConfigIssue], list[ConfigIssue]]:
    """Errors and warnings; each architecture is checked against its own packages."""
    scopes = architecture_scopes(config)
    validator = _Validator(
        config,
        architecture=config.architecture,
        roots=scopes[0].packages if scopes else config.project.root_packages,
        member_owners={
            package: path
            for path, packages in config.workspace_packages.items()
            if path
            for package in packages
        },
    )
    validator.run()
    validator.contracts()
    errors, warnings = validator.errors, validator.warnings
    if (
        scopes
        and not scopes[0].packages
        and config.architecture.has_layout
        and not errors
    ):
        errors.append(
            ConfigIssue(
                "architecture",
                "the workspace root has no selected packages; move layers and features "
                "to the member configs",
            )
        )
    if config.architecture.members and config.project.workspace != "uv":
        errors.append(
            ConfigIssue("architecture.members", "requires project.workspace: uv")
        )
    for path, member_config in config.members.items():
        if (
            member_config.architecture is not None
            and member_config.architecture.members
        ):
            errors.append(
                ConfigIssue(
                    f"members.{path}.architecture.members",
                    "member boundaries are configured in the workspace root",
                )
            )
    for scope in scopes[1:]:
        member = _Validator(
            config,
            architecture=scope.config.architecture,
            roots=scope.packages,
            location=scope.location,
        )
        member.run()
        errors.extend(member.errors)
        warnings.extend(member.warnings)
    return errors, warnings


def _legacy_modules_message(modules: Mapping[str, str]) -> str:
    by_layer: dict[str, list[str]] = {}
    for module, layer in modules.items():
        by_layer.setdefault(layer, []).append(module)
    snippet = "\n".join(
        f"    {layer}:\n      modules: [{', '.join(names)}]"
        for layer, names in by_layer.items()
    )
    return (
        "deprecated and removed in 0.15.0; list each assignment under its "
        f"layer instead:\n  layers:\n{snippet}"
    )


class _Validator:
    def __init__(
        self,
        config: SmeltConfig,
        *,
        architecture: ArchitectureConfig,
        roots: Sequence[str],
        location: str = "architecture",
        member_owners: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.architecture = architecture
        self.roots = list(roots)
        self.location = location
        self._member_owners = member_owners or {}
        self.errors: list[ConfigIssue] = []
        self.warnings: list[ConfigIssue] = []

    def run(self) -> None:
        self.check_layers()
        self._module_sets()

    def check_layers(self) -> None:
        self._layers()
        self._cross_feature()

    def contracts(self) -> None:

        config = self.config
        roots = config.project.root_packages
        structure = config.structure
        for symbol, export in structure.canonical_exports.items():
            if any(name.split(".")[0] not in roots for name in (symbol, export)):
                self._error(
                    f"architecture.canonical_exports.{symbol}",
                    "definition and public export must be inside project.root_packages",
                )
        for name, contract in structure.contracts.items():
            first = contract.match.split(".")[0]
            if first != "*" and first not in roots:
                policy = (
                    "style.overrides"
                    if name in config.style.overrides
                    else "architecture.packages"
                )
                self._error(
                    f"{policy}.{name}.match",
                    f'"{contract.match}" is not inside project.root_packages ({", ".join(roots)})',
                )

    def _layers(self) -> None:
        seen_paths: dict[str, str] = {}
        for name, layer in self.architecture.layers.items():
            base = f"{self.location}.layers.{name}"
            for index, dep in enumerate(layer.may_depend_on):
                self._check_layer(f"{base}.may_depend_on[{index}]", dep)
            if layer.path is None:
                continue
            if layer.path in seen_paths:
                self._error(
                    f"{base}.path",
                    f'duplicate layer path "{layer.path}" '
                    f'(already used by "{seen_paths[layer.path]}")',
                )
            seen_paths.setdefault(layer.path, name)
        cycle = _find_layer_cycle(self.architecture.layers)
        if cycle:
            self.warnings.append(
                ConfigIssue(
                    f"{self.location}.layers",
                    "cyclic may_depend_on: " + " -> ".join(cycle),
                )
            )

    def _cross_feature(self) -> None:
        for index, allowance in enumerate(self.architecture.cross_feature.allow):
            if isinstance(allowance, CrossFeatureAllowance):
                _, source_layer, _, target_layer = allowance.components()
                pair = (source_layer, target_layer)
            else:
                source_layer, target_layer = allowance.split("->", maxsplit=1)
                pair = source_layer.strip(), target_layer.strip()
            for name in pair:
                self._check_layer(f"{self.location}.cross_feature.allow[{index}]", name)

    def _module_sets(self) -> None:
        arch = self.architecture
        named: list[tuple[str, str]] = [
            (f"{self.location}.{key}[{index}]", module)
            for key in ("shared", "composition_root")
            for index, module in enumerate(getattr(arch, key))
        ]
        for module, layer in arch.modules.items():
            self._check_layer(f"{self.location}.modules.{module}", layer)
        if arch.modules:
            self.warnings.append(
                ConfigIssue(
                    f"{self.location}.modules",
                    _legacy_modules_message(arch.modules),
                    deprecated=True,
                )
            )
        bindings = arch.module_bindings()
        named.extend(
            (f"{self.location}.{binding.key}", binding.prefix) for binding in bindings
        )
        if arch.features and arch.features.root:
            named.append((f"{self.location}.features.root", arch.features.root))
        if arch.features and arch.features.pattern:
            prefix = arch.features.pattern.rsplit(".", 1)[0]
            named.append((f"{self.location}.features.pattern", prefix))
        named.extend(
            (f"{self.location}.wiring[{index}]", pattern)
            for index, pattern in enumerate(arch.wiring)
        )
        for path, module in named:
            self._check_scope(path, module)
        self._overlapping_sets(bindings)
        self._check_bindings(bindings)

    def _check_bindings(self, bindings: list[LayerBinding]) -> None:
        by_prefix: dict[str, LayerBinding] = {}
        for binding in bindings:
            existing = by_prefix.get(binding.prefix)
            if existing is not None:
                path = f"{self.location}.{existing.key}"
                if existing.layer != binding.layer:
                    self._error(
                        path,
                        f'"{binding.prefix}" also maps to layer "{binding.layer}" at {self.location}.{binding.key}',
                    )
                else:
                    self.warnings.append(
                        ConfigIssue(
                            path,
                            f'"{binding.prefix}" is also declared at {self.location}.{binding.key}',
                        )
                    )
            by_prefix[binding.prefix] = binding
        for binding in by_prefix.values():
            parent = next(
                (
                    by_prefix[prefix]
                    for prefix in module_prefixes(binding.prefix.rpartition(".")[0])
                    if prefix in by_prefix
                ),
                None,
            )
            if parent is not None and parent.layer == binding.layer:
                self.warnings.append(
                    ConfigIssue(
                        f"{self.location}.{binding.key}",
                        f'redundant layer assignment: "{binding.prefix}" inherits "{parent.layer}" from "{parent.prefix}" ({self.location}.{parent.key})',
                    )
                )

    def _check_scope(self, path: str, module: str) -> None:
        prefix = module.split("*", maxsplit=1)[0].rstrip(".")
        if any(_within(prefix, root) for root in self.roots):
            return
        owner = self._member_owners.get(prefix.split(".", maxsplit=1)[0])
        if owner is not None:
            self._error(
                path,
                f'"{module}" belongs to the workspace member {owner}; '
                "declare it in that member's config",
            )
            return
        where = (
            "project.root_packages"
            if self.location == "architecture"
            else "this member's packages"
        )
        self._error(path, f'"{module}" is not inside {where} ({", ".join(self.roots)})')

    def _check_layer(self, path: str, name: str) -> None:
        layers = self.architecture.layers
        if name not in layers:
            self._error(path, f'unknown layer "{name}"{did_you_mean(name, layers)}')

    def _overlapping_sets(self, bindings: list[LayerBinding]) -> None:
        arch = self.architecture
        features_root = arch.features.root if arch.features else None
        for key in ("shared", "composition_root"):
            for index, module in enumerate(getattr(arch, key)):
                if features_root and _within(module, features_root):
                    self._error(
                        f"{self.location}.{key}[{index}]",
                        f'"{module}" is inside the features root "{features_root}"',
                    )
        for binding in bindings:
            module = binding.prefix
            if features_root and (
                _within(module, features_root) or _within(features_root, module)
            ):
                self._error(
                    f"{self.location}.{binding.key}",
                    f'"{module}" overlaps the features root "{features_root}"; '
                    "modules are for code outside the features",
                )
            for other in (*arch.shared, *arch.composition_root):
                if _within(module, other):
                    self._error(
                        f"{self.location}.{binding.key}",
                        f'"{module}" is already covered by "{other}" '
                        "(shared or composition_root)",
                    )
        for index, module in enumerate(arch.shared):
            for other in arch.composition_root:
                if _within(module, other) or _within(other, module):
                    self._error(
                        f"{self.location}.shared[{index}]",
                        f'"{module}" overlaps composition_root "{other}"',
                    )

    def _error(self, path: str, message: str) -> None:
        self.errors.append(ConfigIssue(path, message))


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _find_layer_cycle(layers: Mapping[str, LayerConfig]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> list[str] | None:
        state[name] = 1
        stack.append(name)
        for dep in layers[name].may_depend_on:
            if dep not in layers or dep == name:
                continue
            if state.get(dep) == 1:
                return [*stack[stack.index(dep) :], dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[name] = 2
        return None

    for name in sorted(layers):
        if name not in state:
            found = visit(name)
            if found:
                return found
    return None
