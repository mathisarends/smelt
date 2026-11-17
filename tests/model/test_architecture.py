from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config import parse_config
from smelt.model import ArchitectureModel, ModuleInfo, ModuleKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smelt.config.models import SmeltConfig

LAYERS = {
    "domain": {"path": "domain"},
    "application": {"path": "application", "may_depend_on": ["domain"]},
    "infrastructure": {"path": "infra.adapters", "may_depend_on": ["application"]},
}


def _config(
    architecture: Mapping[str, object], *, roots: list[str] | None = None
) -> SmeltConfig:
    config, _ = parse_config(
        {
            "version": 1,
            "project": {"root_packages": roots or ["gw"]},
            "architecture": architecture,
        }
    )
    return config


def _model(
    architecture: Mapping[str, object], modules: list[str], packages: list[str]
) -> ArchitectureModel:
    return ArchitectureModel.build(_config(architecture), modules, packages)


MODULES = [
    "gw",
    "gw.bootstrap",
    "gw.shared.clock",
    "gw.features",
    "gw.features.voice",
    "gw.features.voice.domain.session",
    "gw.features.voice.infra.adapters.sql",
    "gw.features.voice.models",
    "gw.features.loose",
    "gw.config",
]
PACKAGES = ["gw", "gw.shared", "gw.features", "gw.features.voice"]
ARCHITECTURE: Mapping[str, object] = {
    "features": {"root": "gw.features"},
    "shared": ["gw.shared"],
    "composition_root": ["gw.bootstrap"],
    "layers": LAYERS,
}


class TestFeatureRoot:
    def test_classifies_layer_inside_feature(self) -> None:
        model = _model(ARCHITECTURE, MODULES, PACKAGES)

        info = model.modules["gw.features.voice.domain.session"]
        assert (info.kind, info.feature, info.layer) == (
            ModuleKind.FEATURE,
            "voice",
            "domain",
        )

    def test_matches_dotted_layer_paths(self) -> None:
        model = _model(ARCHITECTURE, MODULES, PACKAGES)

        assert (
            model.modules["gw.features.voice.infra.adapters.sql"].layer
            == "infrastructure"
        )

    def test_module_outside_layers_keeps_feature(self) -> None:
        model = _model(ARCHITECTURE, MODULES, PACKAGES)

        info = model.modules["gw.features.voice.models"]
        assert (info.feature, info.layer) == ("voice", None)

    def test_plain_module_under_features_root_is_not_a_feature(self) -> None:
        model = _model(ARCHITECTURE, MODULES, PACKAGES)

        assert model.modules["gw.features.loose"].kind is ModuleKind.UNCLASSIFIED

    def test_shared_composition_root_and_unclassified(self) -> None:
        model = _model(ARCHITECTURE, MODULES, PACKAGES)

        kinds = {
            name: model.modules[name].kind
            for name in ("gw.shared.clock", "gw.bootstrap", "gw.config")
        }
        assert kinds == {
            "gw.shared.clock": ModuleKind.SHARED,
            "gw.bootstrap": ModuleKind.COMPOSITION_ROOT,
            "gw.config": ModuleKind.UNCLASSIFIED,
        }

    def test_collects_features_with_packages(self) -> None:
        model = _model(ARCHITECTURE, MODULES, PACKAGES)

        assert {name: f.package for name, f in model.features.items()} == {
            "voice": "gw.features.voice"
        }


class TestFeaturePattern:
    def test_pattern_placeholder_matches_direct_children(self) -> None:
        model = _model(
            {
                "features": {"pattern": "gw.{feature}"},
                "shared": ["gw.shared"],
                "layers": LAYERS,
            },
            ["gw.billing.domain.money", "gw.shared.x"],
            ["gw", "gw.billing", "gw.shared"],
        )

        billing = model.modules["gw.billing.domain.money"]
        assert (billing.feature, billing.layer) == ("billing", "domain")
        assert model.modules["gw.shared.x"].kind is ModuleKind.SHARED


class TestWithoutFeatures:
    def test_layers_apply_to_root_package(self) -> None:
        model = _model(
            {"layers": LAYERS}, ["gw.domain.user", "gw.cli"], ["gw", "gw.domain"]
        )

        user = model.modules["gw.domain.user"]
        assert (user.kind, user.feature, user.layer) == (
            ModuleKind.FEATURE,
            None,
            "domain",
        )
        assert model.modules["gw.cli"].kind is ModuleKind.UNCLASSIFIED

    def test_layer_package_for_root(self) -> None:
        model = _model({"layers": LAYERS}, [], ["gw"])

        assert model.layer_package(None, "infrastructure") == "gw.infra.adapters"


class TestCombinedScopes:
    def test_each_module_is_answered_by_the_architecture_owning_its_package(
        self,
    ) -> None:
        root = _config({"layers": LAYERS}, roots=["gw", "lib"])
        member = _config({"layers": {"core": {"path": "core"}}}, roots=["gw", "lib"])
        modules = ["gw.domain.session", "lib.core.clock", "lib.domain.thing"]
        parts = (
            ArchitectureModel.build(root, modules, ["gw", "lib"], scope=["gw"]),
            ArchitectureModel.build(member, modules, ["gw", "lib"], scope=["lib"]),
        )

        model = ArchitectureModel.combine(root, parts)

        assert parts[0].modules["lib.core.clock"].kind is ModuleKind.UNCLASSIFIED
        assert model.part_for("lib.core.clock") is parts[1]
        assert [model.modules[name].layer for name in modules] == [
            "domain",
            "core",
            None,
        ]
        assert model.info("lib.core.later") == ModuleInfo(
            "lib.core.later", ModuleKind.FEATURE, layer="core"
        )
