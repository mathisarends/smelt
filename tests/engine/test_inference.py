from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config import parse_config
from smelt.config.loader import load_yaml
from smelt.engine.inference import InferredLayer, infer_config, render_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def _layers(layers: list[InferredLayer]) -> list[tuple[str, str, list[str]]]:
    return [(layer.name, layer.path, layer.may_depend_on) for layer in layers]


class TestInferConfig:
    def test_features_root_with_layers(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                "src/shop/__init__.py": "",
                "src/shop/bootstrap.py": "",
                "src/shop/common/__init__.py": "",
                "src/shop/features/__init__.py": "",
                "src/shop/features/cart/__init__.py": "",
                "src/shop/features/cart/domain/__init__.py": "",
                "src/shop/features/cart/infra/__init__.py": "",
                "src/shop/features/orders/__init__.py": "",
                "src/shop/features/orders/domain/__init__.py": "",
                "src/shop/features/orders/use_cases/__init__.py": "",
                "tests/test_cart.py": "",
                "pyproject.toml": '[project]\ndependencies = ["dependency-injector>=4"]\n',
            },
        )

        inferred = infer_config(tmp_path)

        assert inferred is not None
        assert (inferred.source_roots, inferred.root_packages) == (["src"], ["shop"])
        assert inferred.features_root == "shop.features"
        assert inferred.features == ["cart", "orders"]
        assert _layers(inferred.layers) == [
            ("domain", "domain", []),
            ("application", "use_cases", ["domain"]),
            ("infrastructure", "infra", ["domain", "application"]),
        ]
        assert inferred.shared == ["shop.common"]
        assert inferred.composition_root == ["shop.bootstrap"]
        assert inferred.di_frameworks == ["dependency_injector"]
        assert inferred.tests_layout == "none"

    def test_feature_pattern_without_container(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                "app/__init__.py": "",
                "app/billing/__init__.py": "",
                "app/billing/domain/__init__.py": "",
                "app/billing/api/__init__.py": "",
                "app/voice/__init__.py": "",
                "app/voice/domain/__init__.py": "",
                "app/voice/api/__init__.py": "",
                "tests/voice/test_calls.py": "",
            },
        )

        inferred = infer_config(tmp_path)

        assert inferred is not None
        assert inferred.features_pattern == "app.{feature}"
        assert _layers(inferred.layers) == [
            ("domain", "domain", []),
            ("presentation", "api", ["domain"]),
        ]
        assert inferred.tests_layout == "feature"

    def test_plain_package_without_structure(self, tmp_path: Path) -> None:
        write_project(tmp_path, {"tool/__init__.py": "", "tool/cli.py": ""})

        inferred = infer_config(tmp_path)

        assert inferred is not None
        assert (inferred.root_packages, inferred.layers) == (["tool"], [])
        assert inferred.has_features is False

    def test_no_python_package(self, tmp_path: Path) -> None:
        write_project(tmp_path, {"README.md": "hi"})

        assert infer_config(tmp_path) is None


class TestRenderConfig:
    def test_rendered_config_is_valid(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                "src/shop/__init__.py": "",
                "src/shop/main.py": "",
                "src/shop/shared/__init__.py": "",
                "src/shop/features/__init__.py": "",
                "src/shop/features/cart/__init__.py": "",
                "src/shop/features/cart/domain/__init__.py": "",
                "src/shop/features/cart/application/__init__.py": "",
                "tests/cart/test_add.py": "",
            },
        )
        inferred = infer_config(tmp_path)
        assert inferred is not None

        config, warnings = parse_config(load_yaml(render_config(inferred)))

        assert warnings == ()
        assert config.architecture.features is not None
        assert config.architecture.features.root == "shop.features"
        assert config.architecture.cross_feature.pairs() == {
            ("application", "application")
        }
        assert config.tests.layout == "feature"
