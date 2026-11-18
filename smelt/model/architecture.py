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
    A ``central`` module sits outside the features but in a layer
    (``architecture.modules``), so layer rules apply to it as well.
    """

    name: str
    kind: ModuleKind
    feature: str | None = None
    layer: str | None = None
    wiring: bool = False
    central: bool = False

    @property
    def in_grid(self) -> bool:
        return self.kind is ModuleKind.FEATURE


@dataclass(frozen=True, slots=True)
class FeatureInfo:
    name: str
    package: str


def is_within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


@dataclass(frozen=True, slots=True)
class ArchitectureModel:
    """Where every module sits in one architecture.

    ``scope`` limits the architecture to some top-level packages; modules of the
    other packages are unclassified in it. A model combined from ``parts``
    answers each module from the part whose scope contains it.
    """

    config: SmeltConfig
    modules: Mapping[str, ModuleInfo]
    packages: frozenset[str]
    features: Mapping[str, FeatureInfo] = field(default_factory=dict)
    scope: tuple[str, ...] | None = None
    parts: tuple[ArchitectureModel, ...] = ()

    @classmethod
    def build(
        cls,
        config: SmeltConfig,
        modules: Iterable[str],
        packages: Iterable[str],
        *,
        scope: Iterable[str] | None = None,
    ) -> ArchitectureModel:
        package_set = frozenset(packages)
        scoped = None if scope is None else tuple(scope)
        classifier = _Classifier(config, package_set, scoped)
        infos: dict[str, ModuleInfo] = {}
        features: dict[str, FeatureInfo] = {}
        for name in sorted(set(modules)):
            info = classifier.classify(name)
            infos[name] = info
            if info.feature is not None and info.feature not in features:
                package = classifier.feature_package(name)
                if package is not None:
                    features[info.feature] = FeatureInfo(info.feature, package)
        return cls(config, infos, package_set, dict(sorted(features.items())), scoped)

    @classmethod
    def combine(
        cls, config: SmeltConfig, parts: tuple[ArchitectureModel, ...]
    ) -> ArchitectureModel:
        """One view over several scoped architectures; the first part is the root."""
        modules: dict[str, ModuleInfo] = {}
        features: dict[str, FeatureInfo] = {}
        for part in parts:
            modules.update(
                (name, info) for name, info in part.modules.items() if part.owns(name)
            )
            for name, feature in part.features.items():
                features.setdefault(name, feature)
        return cls(
            config,
            dict(sorted(modules.items())),
            parts[0].packages,
            dict(sorted(features.items())),
            parts=parts,
        )

    @property
    def roots(self) -> list[str]:
        """The top-level packages this architecture governs."""
        if self.scope is None:
            return list(self.config.project.root_packages)
        return list(self.scope)

    def owns(self, module: str) -> bool:
        return self.scope is None or module.split(".", maxsplit=1)[0] in self.scope

    def part_for(self, module: str) -> ArchitectureModel:
        """The scoped architecture of ``module``; the model itself if not combined."""
        return next((part for part in self.parts if part.owns(module)), self)

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
        if self.parts:
            return self.part_for(module).info(module)
        return _Classifier(self.config, self.packages, self.scope).classify(module)

    def planned_info(self, module: str, *, is_package: bool) -> ModuleInfo | None:
        """Classify a prospective source path with its implied package parents."""
        if not self.is_first_party(module):
            return None
        part = self.part_for(module)
        package = module if is_package else module.rpartition(".")[0]
        segments = package.split(".")
        parents = {".".join(segments[:end]) for end in range(1, len(segments) + 1)}
        return _Classifier(part.config, part.packages | parents, part.scope).classify(
            module
        )

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
        roots = self.roots
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
        return [root for root in self.roots if root in self.packages]


class _Classifier:
    def __init__(
        self,
        config: SmeltConfig,
        packages: frozenset[str],
        scope: tuple[str, ...] | None = None,
    ) -> None:
        arch = config.architecture
        self._scope = scope
        self._roots = config.project.root_packages if scope is None else list(scope)
        self._shared = arch.shared
        self._composition_root = arch.composition_root
        self._wiring = arch.wiring
        # longest package first, so backend.platform.storage wins over backend.platform
        self._central = sorted(arch.modules.items(), key=lambda item: -len(item[0]))
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

    def classify(self, module: str) -> ModuleInfo:
        if (
            self._scope is not None
            and module.split(".", maxsplit=1)[0] not in self._scope
        ):
            return ModuleInfo(module, ModuleKind.UNCLASSIFIED)
        return self._classify_in_scope(module)

    def feature_package(self, module: str) -> str | None:
        match = self._match_feature(module)
        if match is None:
            return None
        _, rest = match
        segments = module.split(".")
        return ".".join(segments[: len(segments) - len(rest)])

    def _classify_in_scope(self, module: str) -> ModuleInfo:
        wiring = any(module_matches(pattern, module) for pattern in self._wiring)
        if any(is_within(module, root) for root in self._composition_root):
            return ModuleInfo(module, ModuleKind.COMPOSITION_ROOT, wiring=wiring)
        if any(is_within(module, shared) for shared in self._shared):
            return ModuleInfo(module, ModuleKind.SHARED, wiring=wiring)
        central = next(
            (layer for package, layer in self._central if is_within(module, package)),
            None,
        )
        if central is not None:
            return ModuleInfo(
                module, ModuleKind.FEATURE, None, central, wiring, central=True
            )
        if self._features is None:
            layer = self._root_layer(module)
            kind = ModuleKind.UNCLASSIFIED if layer is None else ModuleKind.FEATURE
            return ModuleInfo(module, kind, None, layer, wiring)
        match = self._match_feature(module)
        if match is None:
            return ModuleInfo(module, ModuleKind.UNCLASSIFIED, wiring=wiring)
        feature, rest = match
        return ModuleInfo(
            module,
            ModuleKind.FEATURE,
            feature,
            self._match_layer(rest),
            wiring,
        )

    def _root_layer(self, module: str) -> str | None:
        """Without features, layers sit directly in a root package."""
        for root in self._roots:
            if module.startswith(f"{root}."):
                return self._match_layer(module[len(root) + 1 :].split("."))
        return None

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
