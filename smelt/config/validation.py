from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config.errors import ConfigIssue, did_you_mean
from smelt.config.models import PATCH_CATEGORIES, CrossFeatureAllowance

if TYPE_CHECKING:
    from smelt.config.models import SmeltConfig


def validate_semantics(
    config: SmeltConfig,
) -> tuple[list[ConfigIssue], list[ConfigIssue]]:
    validator = _Validator(config)
    validator.run()
    return validator.errors, validator.warnings


class _Validator:
    def __init__(self, config: SmeltConfig) -> None:
        self.config = config
        self.errors: list[ConfigIssue] = []
        self.warnings: list[ConfigIssue] = []

    def run(self) -> None:
        self._layers()
        self._cross_feature()
        self._module_sets()
        self._roles()
        self._tests()

    def _error(self, path: str, message: str) -> None:
        self.errors.append(ConfigIssue(path, message))

    def _check_layer(self, path: str, name: str) -> None:
        layers = self.config.architecture.layers
        if name not in layers:
            self._error(path, f'unknown layer "{name}"{did_you_mean(name, layers)}')

    def _layers(self) -> None:
        seen_paths: dict[str, str] = {}
        for name, layer in self.config.architecture.layers.items():
            base = f"architecture.layers.{name}"
            for index, dep in enumerate(layer.may_depend_on):
                self._check_layer(f"{base}.may_depend_on[{index}]", dep)
            if layer.path in seen_paths:
                self._error(
                    f"{base}.path",
                    f'duplicate layer path "{layer.path}" '
                    f'(already used by "{seen_paths[layer.path]}")',
                )
            seen_paths.setdefault(layer.path, name)
        cycle = _find_layer_cycle(self.config)
        if cycle:
            self.warnings.append(
                ConfigIssue(
                    "architecture.layers", "cyclic may_depend_on: " + " -> ".join(cycle)
                )
            )

    def _cross_feature(self) -> None:
        for index, allowance in enumerate(self.config.architecture.cross_feature.allow):
            if isinstance(allowance, CrossFeatureAllowance):
                _, source_layer, _, target_layer = allowance.components()
                pair = (source_layer, target_layer)
            else:
                source_layer, target_layer = allowance.split("->", maxsplit=1)
                pair = source_layer.strip(), target_layer.strip()
            for name in pair:
                self._check_layer(f"architecture.cross_feature.allow[{index}]", name)

    def _module_sets(self) -> None:
        arch = self.config.architecture
        named: list[tuple[str, str]] = [
            (f"architecture.{key}[{index}]", module)
            for key in ("shared", "composition_root")
            for index, module in enumerate(getattr(arch, key))
        ]
        if arch.features and arch.features.root:
            named.append(("architecture.features.root", arch.features.root))
        if arch.features and arch.features.pattern:
            prefix = arch.features.pattern.rsplit(".", 1)[0]
            named.append(("architecture.features.pattern", prefix))
        roots = self.config.project.root_packages
        for path, module in named:
            if not any(_within(module, root) for root in roots):
                self._error(
                    path,
                    f'"{module}" is not inside project.root_packages ({", ".join(roots)})',
                )
        for index, pattern in enumerate(arch.wiring):
            prefix = pattern.split("*")[0].rstrip(".")
            if not any(_within(prefix, root) for root in roots):
                self._error(
                    f"architecture.wiring[{index}]",
                    f'"{pattern}" is not inside project.root_packages ({", ".join(roots)})',
                )
        self._overlapping_sets()

    def _overlapping_sets(self) -> None:
        arch = self.config.architecture
        features_root = arch.features.root if arch.features else None
        for key in ("shared", "composition_root"):
            for index, module in enumerate(getattr(arch, key)):
                if features_root and _within(module, features_root):
                    self._error(
                        f"architecture.{key}[{index}]",
                        f'"{module}" is inside the features root "{features_root}"',
                    )
        for index, module in enumerate(arch.shared):
            for other in arch.composition_root:
                if _within(module, other) or _within(other, module):
                    self._error(
                        f"architecture.shared[{index}]",
                        f'"{module}" overlaps composition_root "{other}"',
                    )

    def _roles(self) -> None:
        roles = self.config.roles
        for name, role in roles.items():
            base = f"roles.{name}"
            for index, layer in enumerate(role.layers):
                self._check_layer(f"{base}.layers[{index}]", layer)
            target = role.detect.implements
            if target == name:
                self._error(
                    f"{base}.detect.implements", "a role cannot implement itself"
                )
            elif target is not None and target not in roles:
                self._error(
                    f"{base}.detect.implements",
                    f'unknown role "{target}"{did_you_mean(target, roles)}',
                )

    def _tests(self) -> None:
        tests = self.config.tests
        categories = sorted(
            PATCH_CATEGORIES
            | set(self.config.architecture.layers)
            | {"composition_root"}
        )
        for key in ("allow", "forbid"):
            for index, value in enumerate(getattr(tests.patching, key)):
                if value not in categories:
                    self._error(
                        f"tests.patching.{key}[{index}]",
                        f'unknown category "{value}"{did_you_mean(value, categories)}',
                    )
        for index, layer in enumerate(tests.mocks.forbid_first_party):
            self._check_layer(f"tests.mocks.forbid_first_party[{index}]", layer)
        if tests.layout == "feature" and self.config.architecture.features is None:
            self._error(
                "tests.layout", '"feature" layout requires architecture.features'
            )


def _within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _find_layer_cycle(config: SmeltConfig) -> list[str] | None:
    layers = config.architecture.layers
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
