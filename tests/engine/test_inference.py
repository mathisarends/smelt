from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import parse_config
from smelt.config.loader import load_yaml
from smelt.engine.inference import InferredLayer, infer_config, render_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def _layers(layers: list[InferredLayer]) -> list[tuple[str, str, list[str]]]:
    return [(layer.name, layer.path, layer.may_depend_on) for layer in layers]


class TestInferConfig:
    def test_presentation_may_use_domain_types_for_mapping(
        self, tmp_path: Path
    ) -> None:
        write_project(
            tmp_path,
            {
                "src/shop/__init__.py": "",
                "src/shop/domain/__init__.py": "",
                "src/shop/application/__init__.py": "",
                "src/shop/presentation/__init__.py": "",
            },
        )

        inferred = infer_config(tmp_path)

        assert inferred is not None
        assert _layers(inferred.layers) == [
            ("domain", "domain", []),
            ("application", "application", ["domain"]),
            ("presentation", "presentation", ["application", "domain"]),
        ]

    def test_uv_workspace_with_nested_source_roots(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                "pyproject.toml": (
                    '[tool.uv.workspace]\nmembers = ["backend", "libs/*"]\n'
                ),
                "backend/pyproject.toml": '[project]\ndependencies = ["dishka>=1"]\n',
                "backend/src/backend/__init__.py": "",
                "backend/src/backend/main.py": "",
                "backend/src/backend/lifespan.py": "",
                "backend/src/backend/features/auth/__init__.py": "",
                "backend/src/backend/features/auth/domain/__init__.py": "",
                "backend/src/backend/features/auth/infrastructure/di.py": (
                    "from dishka import Provider\nclass AuthProvider(Provider): pass\n"
                ),
                "backend/tests/test_auth.py": "",
                "libs/agent/src/agent/__init__.py": "",
                "libs/agent/tests/test_agent.py": "",
                "libs/tokens/src/tokens/__init__.py": "",
            },
        )

        inferred = infer_config(tmp_path)

        assert inferred is not None
        assert inferred.root_packages == ["backend", "agent", "tokens"]
        assert inferred.source_roots == [
            "backend/src",
            "libs/agent/src",
            "libs/tokens/src",
        ]
        assert inferred.test_roots == ["backend/tests", "libs/agent/tests"]
        assert inferred.features_root == "backend.features"
        assert inferred.composition_root == ["backend.main", "backend.lifespan"]
        assert inferred.wiring == ["backend.features.auth.infrastructure.di"]
        config, warnings = parse_config(load_yaml(render_config(inferred)))
        assert warnings == ()
        assert config.architecture.wiring == inferred.wiring

    def test_namespace_member_joins_and_skipped_members_are_named(
        self, tmp_path: Path
    ) -> None:
        write_project(
            tmp_path,
            {
                "pyproject.toml": (
                    '[tool.uv.workspace]\nmembers = ["backend", "e2e/stack", "web"]\n'
                ),
                "backend/src/backend/__init__.py": "",
                "e2e/stack/pyproject.toml": (
                    "[tool.uv.build-backend]\nnamespace = true\n"
                ),
                "e2e/stack/src/e2e_stack/server/__init__.py": "",
                "e2e/stack/tests/test_server.py": "",
                "web/package.json": "{}",
            },
        )

        inferred = infer_config(tmp_path)

        assert inferred is not None
        assert inferred.root_packages == ["backend", "e2e_stack"]
        assert inferred.source_roots == ["backend/src", "e2e/stack/src"]
        assert inferred.test_roots == ["e2e/stack/tests"]
        rendered = render_config(inferred)
        assert (
            "  # not analyzed: workspace member web "
            "(no Python package in src/ or the member directory)\n"
        ) in rendered
        config, warnings = parse_config(load_yaml(rendered))
        assert warnings == ()
        assert config.project.root_packages == ["backend", "e2e_stack"]

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
        assert inferred.tests_layout == "none"

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
    def test_adoption_exposes_coverage_decisions(self, tmp_path: Path) -> None:
        inferred = infer_config(write_project(tmp_path, DDD_WORKSPACE))
        assert inferred is not None

        rendered = render_config(inferred)

        assert "third_party: allow" in rendered
        assert "third_party: {default: deny" in rendered
        assert "transitive: false  # direct imports only" in rendered
        assert "pair applies to ALL features" in rendered

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
        assert config.tests.layout == "none"


DISHKA_PROVIDER = "from dishka import Provider\nclass P(Provider): pass\n"

DDD_WORKSPACE = {
    "pyproject.toml": '[tool.uv.workspace]\nmembers = ["backend", "libs/*"]\n',
    "backend/src/backend/__init__.py": "",
    "backend/src/backend/main.py": "from backend.app import create_app\n",
    "backend/src/backend/app.py": (
        "from backend.lifespan import lifespan\n"
        "from backend.platform.database.di import DatabaseProvider\n"
    ),
    "backend/src/backend/lifespan.py": "",
    "backend/src/backend/env.py": "",
    "backend/src/backend/helpers.py": "from backend.lifespan import lifespan\n",
    "backend/src/backend/platform/__init__.py": "",
    "backend/src/backend/platform/database/__init__.py": "",
    "backend/src/backend/platform/database/orm.py": "",
    "backend/src/backend/platform/database/di.py": DISHKA_PROVIDER,
    "backend/src/backend/platform/storage/__init__.py": "",
    "backend/src/backend/platform/storage/di.py": DISHKA_PROVIDER,
    "backend/src/backend/presentation/__init__.py": "",
    "backend/src/backend/presentation/middleware.py": "",
    "backend/src/backend/features/__init__.py": "",
    "backend/src/backend/features/auth/__init__.py": "",
    "backend/src/backend/features/auth/domain/__init__.py": "",
    "backend/src/backend/features/auth/domain/token.py": "",
    "backend/src/backend/features/auth/infrastructure/__init__.py": "",
    "backend/src/backend/features/auth/infrastructure/di.py": DISHKA_PROVIDER,
    "backend/src/backend/features/user/__init__.py": "",
    "backend/src/backend/features/user/domain/__init__.py": "",
    "backend/src/backend/features/user/infrastructure/__init__.py": "",
    "backend/src/backend/features/user/infrastructure/di.py": DISHKA_PROVIDER,
    "backend/tests/backend/features/auth/domain/test_token.py": "",
    "backend/tests/backend/features/auth/domain/test_auth_domain_misc.py": "",
    "libs/agent/src/agent/__init__.py": "",
    "libs/agent/src/agent/tools.py": "",
    "libs/agent/tests/agent/test_tools.py": "",
}


class TestDddWorkspace:
    def test_app_factory_joins_the_composition_root(self, tmp_path: Path) -> None:
        inferred = infer_config(write_project(tmp_path, DDD_WORKSPACE))

        assert inferred is not None
        assert inferred.composition_root == [
            "backend.main",
            "backend.lifespan",
            "backend.app",
        ]

    def test_app_factory_reaching_the_wiring_through_facades(
        self, tmp_path: Path
    ) -> None:
        workspace = {
            **DDD_WORKSPACE,
            # like fastapi-canon: app.py assembles the features via their facade
            "backend/src/backend/app.py": "from backend.features import FEATURES\n",
            "backend/src/backend/features/__init__.py": (
                "from .auth import AuthProvider\nFEATURES = [AuthProvider]\n"
            ),
            "backend/src/backend/features/auth/__init__.py": (
                "from .infrastructure.di import P as AuthProvider\n"
            ),
        }

        inferred = infer_config(write_project(tmp_path, workspace))

        assert inferred is not None
        assert "backend.app" in inferred.composition_root

    def test_facade_without_wiring_does_not_make_a_composition_root(
        self, tmp_path: Path
    ) -> None:
        workspace = {
            **DDD_WORKSPACE,
            "backend/src/backend/app.py": "from backend.features import auth\n",
            "backend/src/backend/features/auth/__init__.py": (
                "from .domain.token import Token\n"
            ),
        }

        inferred = infer_config(write_project(tmp_path, workspace))

        assert inferred is not None
        assert "backend.app" not in inferred.composition_root

    @pytest.mark.parametrize(
        ("bootstrap", "is_factory"),
        [
            # shop/bootstrap/__init__.py: ``..app`` is shop/app.py
            ("from ..app import create_app\n", True),
            # ``. import app`` is shop/bootstrap/app.py, not shop/app.py
            ("from . import app\n", False),
        ],
    )
    def test_relative_imports_of_a_composition_root_package(
        self, tmp_path: Path, bootstrap: str, *, is_factory: bool
    ) -> None:
        files = {
            "shop/__init__.py": "",
            "shop/providers.py": DISHKA_PROVIDER,
            "shop/app.py": "from shop.providers import P\n",
            "shop/bootstrap/__init__.py": bootstrap,
            "shop/bootstrap/app.py": "",
        }

        inferred = infer_config(write_project(tmp_path, files))

        assert inferred is not None
        assert ("shop.app" in inferred.composition_root) is is_factory

    def test_server_target_string_and_assembly_modules(self, tmp_path: Path) -> None:
        workspace = {
            **DDD_WORKSPACE,
            # main.py names the app only as a string, the way uvicorn takes it
            "backend/src/backend/main.py": (
                'import uvicorn\nuvicorn.run("backend.app:app", port=8000)\n'
            ),
            "backend/src/backend/app.py": (
                "from web import App, Bundle\n"
                "from backend.features import FEATURES\n"
                "from backend.platform import platform_bundle\n"
                "def create_app() -> App:\n"
                "    return App(platform_bundle, *FEATURES)\n"
                "app = create_app()\n"
            ),
            "backend/src/backend/features/__init__.py": (
                "from . import auth\nFEATURES = (auth.bundle,)\n"
            ),
            "backend/src/backend/features/auth/__init__.py": (
                "from .bundle import bundle\n"
            ),
            "backend/src/backend/features/auth/bundle.py": (
                "from web import Bundle\n"
                "from .infrastructure.di import P as AuthProvider\n"
                "bundle = Bundle(providers=[AuthProvider])\n"
            ),
            "backend/src/backend/platform/__init__.py": (
                "from .assembly import platform_bundle\n"
            ),
            "backend/src/backend/platform/database/__init__.py": (
                "from .di import P as DatabaseProvider\n"
            ),
            "backend/src/backend/platform/assembly.py": (
                "from web import Bundle\n"
                "from backend.platform.database import DatabaseProvider\n"
                "platform_bundle = Bundle(providers=[DatabaseProvider])\n"
            ),
        }

        inferred = infer_config(write_project(tmp_path, workspace))

        assert inferred is not None
        assert inferred.composition_root == [
            "backend.main",
            "backend.lifespan",
            "backend.app",
        ]
        assert inferred.evidence["backend.app"].startswith(
            'backend.main serves "backend.app:app" from it; backend.app → '
        )
        assert "backend.features.auth.bundle" in inferred.wiring
        assert "backend.platform.assembly" in inferred.wiring
        assert inferred.evidence["backend.features.auth.bundle"] == (
            "backend.app → backend.features.FEATURES → "
            "backend.features.auth.bundle.bundle → "
            "backend.features.auth.infrastructure.di.P"
        )
        rendered = render_config(inferred)
        assert "    # assembles providers: backend.app → " in rendered
        config, _ = parse_config(load_yaml(rendered))
        assert "backend.app" in config.architecture.composition_root

    def test_framework_consumers_are_no_assembly(self, tmp_path: Path) -> None:
        workspace = {
            **DDD_WORKSPACE,
            "backend/src/backend/app.py": (
                "from web import App\n"
                "from backend.lifespan import lifespan\n"
                "from backend.features.auth.presentation.router import router\n"
                "from backend.features.auth.application.service import Service\n"
                "app = App(lifespan, routers=[router], service=Service)\n"
            ),
            "backend/src/backend/features/auth/presentation/__init__.py": "",
            "backend/src/backend/features/auth/presentation/router.py": (
                "from web import Router\nrouter = Router()\n"
            ),
            # imports the wiring, but nothing it defines is built from it
            "backend/src/backend/features/auth/application/__init__.py": "",
            "backend/src/backend/features/auth/application/service.py": (
                "from backend.features.auth.infrastructure.di import P\n"
                "class Service: ...\n"
            ),
        }

        inferred = infer_config(write_project(tmp_path, workspace))

        assert inferred is not None
        assert not any("presentation" in w for w in inferred.wiring)
        assert not any("application" in w for w in inferred.wiring)

    @pytest.mark.parametrize(
        ("other_importer", "confidence"), [(False, "medium"), (True, "low")]
    )
    def test_unclassified_package_of_the_root_is_only_a_candidate(
        self, tmp_path: Path, *, other_importer: bool, confidence: str
    ) -> None:
        workspace = {
            **DDD_WORKSPACE,
            "backend/src/backend/app.py": (
                "from backend.lifespan import lifespan\n"
                "from backend.rpc import endpoint\n"
                "app = (lifespan, endpoint)\n"
            ),
            "backend/src/backend/rpc/__init__.py": "from .routes import endpoint\n",
            "backend/src/backend/rpc/routes.py": "endpoint = object()\n",
        }
        if other_importer:
            workspace["backend/src/backend/features/auth/domain/token.py"] = (
                "from backend.rpc import endpoint\n"
            )

        inferred = infer_config(write_project(tmp_path, workspace))

        assert inferred is not None
        assert "backend.rpc" not in inferred.composition_root
        [candidate] = inferred.candidates
        assert (candidate.module, candidate.confidence) == ("backend.rpc", confidence)
        assert candidate.chain == ["backend.app", "backend.rpc.routes.endpoint"]
        rendered = render_config(inferred)
        assert "    # - backend.rpc\n" in rendered
        config, _ = parse_config(load_yaml(rendered))
        assert "backend.rpc" not in config.architecture.composition_root

    def test_settings_modules_are_shared(self, tmp_path: Path) -> None:
        inferred = infer_config(write_project(tmp_path, DDD_WORKSPACE))

        assert inferred is not None
        assert inferred.shared == ["backend.env"]

    def test_central_packages_get_a_layer(self, tmp_path: Path) -> None:
        inferred = infer_config(write_project(tmp_path, DDD_WORKSPACE))

        assert inferred is not None
        # no feature has a presentation layer, so backend.presentation stays open
        assert inferred.modules == {"backend.platform": "infrastructure"}
        assert inferred.unclassified_roots == ["agent"]

    def test_wiring_collapses_into_patterns(self, tmp_path: Path) -> None:
        inferred = infer_config(write_project(tmp_path, DDD_WORKSPACE))

        assert inferred is not None
        assert inferred.wiring == [
            "backend.features.*.infrastructure.di",
            "backend.platform.*.di",
        ]

    def test_mirror_layout_with_root_package(self, tmp_path: Path) -> None:
        inferred = infer_config(write_project(tmp_path, DDD_WORKSPACE))

        assert inferred is not None
        assert inferred.tests_layout == "mirror"
        config, warnings = parse_config(load_yaml(render_config(inferred)))
        assert warnings == ()
        assert config.tests.mirror == "{root}/{path}/test_{module}.py"
        assert config.architecture.modules == inferred.modules
