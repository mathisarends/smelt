from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from smelt.config.patterns import module_matches

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from smelt.config.models import LayerConfig, SmeltConfig


class ModuleKind(StrEnum):
    FEATURE = "feature"
    SHARED = "shared"
    COMPOSITION_ROOT = "composition_root"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    """Where a module sits in the architecture.

    ``kind`` is ``FEATURE`` for every module in the feature/layer grid. When no
    features are configured, the grid spans the root packages and ``feature`` is None.
    """

    name: str
    kind: ModuleKind
    feature: str | None = None
    layer: str | None = None
    roles: frozenset[str] = frozenset()
    wiring: bool = False

    @property
    def in_grid(self) -> bool:
        return self.kind is ModuleKind.FEATURE


@dataclass(frozen=True, slots=True)
class FeatureInfo:
    name: str
    package: str


def is_within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


@dataclass(frozen=True)
class ArchitectureModel:
    config: SmeltConfig
    modules: Mapping[str, ModuleInfo]
    packages: frozenset[str]
    features: Mapping[str, FeatureInfo] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        config: SmeltConfig,
        modules: Iterable[str],
        packages: Iterable[str],
        roles: Mapping[str, frozenset[str]] | None = None,
    ) -> ArchitectureModel:
        package_set = frozenset(packages)
        classifier = _Classifier(config, package_set)
        infos: dict[str, ModuleInfo] = {}
        features: dict[str, FeatureInfo] = {}
        for name in sorted(set(modules)):
            info = classifier.classify(name, (roles or {}).get(name, frozenset()))
            infos[name] = info
            if info.feature is not None and info.feature not in features:
                package = classifier.feature_package(name)
                if package is not None:
                    features[info.feature] = FeatureInfo(info.feature, package)
        return cls(config, infos, package_set, dict(sorted(features.items())))

    def with_roles(self, roles: Mapping[str, frozenset[str]]) -> ArchitectureModel:
        return ArchitectureModel.build(self.config, self.modules, self.packages, roles)

    @property
    def layers(self) -> Mapping[str, LayerConfig]:
        return self.config.architecture.layers

    @property
    def has_features(self) -> bool:
        return self.config.architecture.features is not None

    def info(self, module: str) -> ModuleInfo | None:
        info = self.modules.get(module)
        if info is not None:
            return info
        if not self.is_first_party(module):
            return None
        return _Classifier(self.config, self.packages).classify(module, frozenset())

    def is_first_party(self, module: str) -> bool:
        return any(
            is_within(module, root) for root in self.config.project.root_packages
        )

    def is_package(self, module: str) -> bool:
        return module in self.packages

    def layer_package(self, feature: str | None, layer: str) -> str | None:
        """The dotted package of ``layer`` inside ``feature`` (or the root package)."""
        path = self.layers[layer].path
        if feature is not None:
            info = self.features.get(feature)
            if info is not None:
                return f"{info.package}.{path}"
            return self.feature_package_for(feature) + f".{path}"
        roots = self.config.project.root_packages
        return f"{roots[0]}.{path}" if roots else None

    def feature_package_for(self, feature: str) -> str:
        if feature in self.features:
            return self.features[feature].package
        spec = self.config.architecture.features
        if spec is not None and spec.root is not None:
            return f"{spec.root}.{feature}"
        if spec is not None and spec.pattern is not None:
            return spec.pattern.replace("{feature}", feature)
        return feature

    def feature_pattern(self) -> str | None:
        spec = self.config.architecture.features
        if spec is None:
            return None
        if spec.root is not None:
            return f"{spec.root}.{{feature}}"
        return spec.pattern

    def modules_in(self, feature: str | None, layer: str) -> list[str]:
        return [
            name
            for name, info in self.modules.items()
            if info.in_grid and info.feature == feature and info.layer == layer
        ]

    def grid_containers(self) -> list[str]:
        """Packages whose direct child packages must be declared layers."""
        if not self.layers:
            return []
        if self.has_features:
            return sorted(info.package for info in self.features.values())
        return [
            root for root in self.config.project.root_packages if root in self.packages
        ]


class _Classifier:
    def __init__(self, config: SmeltConfig, packages: frozenset[str]) -> None:
        arch = config.architecture
        self._roots = config.project.root_packages
        self._shared = arch.shared
        self._composition_root = arch.composition_root
        self._wiring = arch.wiring
        self._packages = packages
        self._layer_paths = sorted(
            ((layer.path.split("."), name) for name, layer in arch.layers.items()),
            key=lambda item: -len(item[0]),
        )
        self._features = arch.features
        self._feature_prefix: list[str] | None = None
        if arch.features is not None and arch.features.root is not None:
            self._feature_prefix = arch.features.root.split(".")
        elif arch.features is not None and arch.features.pattern is not None:
            self._feature_prefix = arch.features.pattern.split(".")[:-1]

    def classify(self, module: str, roles: frozenset[str]) -> ModuleInfo:
        wiring = any(module_matches(pattern, module) for pattern in self._wiring)
        if any(is_within(module, root) for root in self._composition_root):
            return ModuleInfo(
                module, ModuleKind.COMPOSITION_ROOT, roles=roles, wiring=wiring
            )
        if any(is_within(module, shared) for shared in self._shared):
            return ModuleInfo(module, ModuleKind.SHARED, roles=roles, wiring=wiring)
        if self._features is None:
            for root in self._roots:
                if module.startswith(f"{root}."):
                    rest = module[len(root) + 1 :].split(".")
                    layer = self._match_layer(rest)
                    if layer is None:
                        break
                    return ModuleInfo(
                        module, ModuleKind.FEATURE, None, layer, roles, wiring
                    )
            return ModuleInfo(
                module, ModuleKind.UNCLASSIFIED, roles=roles, wiring=wiring
            )
        match = self._match_feature(module)
        if match is None:
            return ModuleInfo(
                module, ModuleKind.UNCLASSIFIED, roles=roles, wiring=wiring
            )
        feature, rest = match
        return ModuleInfo(
            module,
            ModuleKind.FEATURE,
            feature,
            self._match_layer(rest),
            roles,
            wiring,
        )

    def feature_package(self, module: str) -> str | None:
        match = self._match_feature(module)
        if match is None:
            return None
        _, rest = match
        segments = module.split(".")
        return ".".join(segments[: len(segments) - len(rest)])

    def _match_feature(self, module: str) -> tuple[str, list[str]] | None:
        prefix = self._feature_prefix
        if prefix is None:
            return None
        segments = module.split(".")
        index = len(prefix)
        if len(segments) <= index or segments[:index] != prefix:
            return None
        if ".".join(segments[: index + 1]) not in self._packages:
            return None
        return segments[index], segments[index + 1 :]

    def _match_layer(self, rest: list[str]) -> str | None:
        for path, name in self._layer_paths:
            if rest[: len(path)] == path:
                return name
        return None
