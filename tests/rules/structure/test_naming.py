from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.engine.check import CheckOptions
from tests.helpers import codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
structure:
  forbidden_names: [utils, helpers]
"""


class TestForbiddenPackageName:
    def test_packages_and_modules(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": CONFIG,
                "app/__init__.py": "",
                "app/utils/__init__.py": "",
                "app/utils/text.py": "",
                "app/billing/__init__.py": "",
                "app/billing/helpers.py": "",
                "app/billing/useful.py": "",
            },
        )

        found = violations(root, CheckOptions(select=("SMT302",)))

        assert codes_at(found) == [
            ("SMT302", "app/billing/helpers.py", None),
            ("SMT302", "app/utils/__init__.py", None),
        ]
        assert found[1].message == 'package name "utils" is not allowed (app.utils)'

    def test_no_names_configured(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": "",
                "app/utils.py": "",
            },
        )

        assert violations(root, CheckOptions(select=("SMT302",))) == []


MODULE_CONFIG = """
version: 1
project:
  root_packages: [app]
structure:
  contracts:
    module-names:
      match: app
      private_modules: forbid
"""
MODULE_OPTIONS = CheckOptions(select=("SMT312",))


def _module_project(
    root: Path, files: dict[str, str], config: str = MODULE_CONFIG
) -> Path:
    return write_project(root, {"smelt.yml": config, "app/__init__.py": "", **files})


class TestNoPrivateModules:
    @pytest.mark.parametrize(
        "name", ["_service", "__service", "__service__", "_", "__", "__about__"]
    )
    def test_underscore_prefixed_module_names(self, tmp_path: Path, name: str) -> None:
        root = _module_project(tmp_path, {f"app/{name}.py": "VALUE = 1\n"})

        found = violations(root, MODULE_OPTIONS)

        assert codes_at(found) == [("SMT312", f"app/{name}.py", None)]
        assert found[0].expected is not None
        assert found[0].expected["kind"] == "module"
        assert found[0].expected["contract"] == "module-names"

    @pytest.mark.parametrize(
        "name", ["_internal", "__internal", "__main__", "__init__"]
    )
    def test_packages_are_checked_once_without_inherited_child_noise(
        self, tmp_path: Path, name: str
    ) -> None:
        root = _module_project(
            tmp_path, {f"app/{name}/__init__.py": "", f"app/{name}/service.py": ""}
        )

        found = violations(root, MODULE_OPTIONS)

        assert codes_at(found) == [("SMT312", f"app/{name}/__init__.py", None)]
        assert found[0].expected is not None
        assert found[0].expected["kind"] == "package"

    def test_special_python_files_and_private_helpers_are_valid(
        self, tmp_path: Path
    ) -> None:
        root = _module_project(
            tmp_path,
            {
                "app/__main__.py": "",
                "app/service.py": "def run(): return _prepare()\ndef _prepare(): return 1\nclass _State: pass\n",
                "app/order_store.py": "",
                "app/service_.py": "",
            },
        )

        assert violations(root, MODULE_OPTIONS) == []

    def test_exports_do_not_exempt_a_private_module_name(self, tmp_path: Path) -> None:
        root = _module_project(
            tmp_path,
            {
                "app/__init__.py": "from ._service import run as run\n",
                "app/_service.py": "__all__ = ['run']\ndef run(): pass\n",
            },
        )

        assert [v.source_module for v in violations(root, MODULE_OPTIONS)] == [
            "app._service"
        ]

    @pytest.mark.parametrize(
        "config",
        [
            "version: 1\nproject: {root_packages: [app]}\n",
            MODULE_CONFIG.replace("private_modules: forbid", "private_modules: allow"),
        ],
    )
    def test_default_and_explicit_allow_preserve_existing_behavior(
        self, tmp_path: Path, config: str
    ) -> None:
        root = _module_project(tmp_path, {"app/_service.py": ""}, config)

        assert violations(root, MODULE_OPTIONS) == []

    def test_inner_package_can_explicitly_allow_private_names(
        self, tmp_path: Path
    ) -> None:
        config = (
            MODULE_CONFIG
            + "    compatibility:\n      match: app._vendor\n      private_modules: allow\n"
        )
        root = _module_project(
            tmp_path,
            {
                "app/_vendor/__init__.py": "",
                "app/_vendor/_driver.py": "",
                "app/_service.py": "",
            },
            config,
        )

        assert [v.source_module for v in violations(root, MODULE_OPTIONS)] == [
            "app._service"
        ]

    def test_only_the_selected_packages_follow_the_policy(self, tmp_path: Path) -> None:
        root = _module_project(
            tmp_path,
            {
                "app/api/__init__.py": "",
                "app/api/_endpoint.py": "",
                "app/_compatibility.py": "",
            },
            MODULE_CONFIG.replace("match: app", "match: app.api"),
        )

        assert [v.source_module for v in violations(root, MODULE_OPTIONS)] == [
            "app.api._endpoint"
        ]

    def test_root_package_itself_is_checked(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": MODULE_CONFIG.replace("[app]", "[_app]").replace(
                    "match: app", "match: _app"
                ),
                "_app/__init__.py": "",
                "_app/service.py": "",
            },
        )

        assert [v.source_module for v in violations(root, MODULE_OPTIONS)] == ["_app"]

    def test_split_namespace_packages_are_reported_once(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": MODULE_CONFIG.replace(
                    "root_packages: [app]",
                    "root_packages: [app]\n  source_roots: [left, right]",
                ),
                "left/app/_internal/one.py": "",
                "right/app/_internal/two.py": "",
            },
        )

        found = violations(root, MODULE_OPTIONS)

        assert [v.source_module for v in found] == ["app._internal"]
        assert found[0].path == "left/app/_internal"

    def test_build_directory_spelling_is_not_a_module_name(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": MODULE_CONFIG.replace(
                    "root_packages: [app]",
                    "root_packages: [app]\n  source_roots: [_source]",
                ),
                "_source/app/service.py": "",
            },
        )

        assert violations(root, MODULE_OPTIONS) == []

    def test_excluded_source_is_not_checked(self, tmp_path: Path) -> None:
        root = _module_project(
            tmp_path,
            {"app/_generated.py": ""},
            MODULE_CONFIG.replace(
                "root_packages: [app]",
                "root_packages: [app]\n  exclude: [app/_generated.py]",
            ),
        )

        assert violations(root, MODULE_OPTIONS) == []

    def test_contradictory_policies_for_one_package_are_config_errors(
        self, tmp_path: Path
    ) -> None:
        config = (
            MODULE_CONFIG
            + "    contradictory:\n      match: app\n      private_modules: allow\n"
        )
        root = _module_project(tmp_path, {"app/_service.py": ""}, config)

        with pytest.raises(
            ConfigError, match="conflicting private module naming policies"
        ):
            violations(root, MODULE_OPTIONS)


CLASS_CONFIG = """
version: 1
project:
  root_packages: [app]
structure:
  contracts:
    class-names:
      match: app
      rules: {no-base-class-prefix: error}
"""
CLASS_OPTIONS = CheckOptions(select=("SMT323",))


class TestNoBaseClassPrefix:
    @pytest.mark.parametrize(
        "name",
        [
            "BaseService",
            "_BaseService",
            "__BaseService",
            "BaseHTTPClient",
            "Base_Service",
            "Base",
            "_Base",
        ],
    )
    def test_definitions_are_reported_at_the_class_without_renaming(
        self, tmp_path: Path, name: str
    ) -> None:
        source = f"@decorate\nclass {name}:\n    pass\n"
        root = _module_project(tmp_path, {"app/service.py": source}, CLASS_CONFIG)

        found = violations(root)

        assert codes_at(found) == [("SMT323", "app/service.py", 2)]
        assert found[0].column == 1
        assert found[0].source_module == "app.service"
        assert found[0].expected == {
            "forbidden_prefix": "Base",
            "class": name,
            "automatic_rename": False,
        }
        assert (root / "app/service.py").read_text(encoding="utf-8") == source

    @pytest.mark.parametrize(
        "name",
        [
            "Service",
            "_Service",
            "Baseline",
            "_Baseline",
            "Baseball",
            "Base64",
            "Base64Encoder",
            "Database",
            "ServiceBase",
        ],
    )
    def test_nearby_names_are_valid(self, tmp_path: Path, name: str) -> None:
        root = _module_project(
            tmp_path, {"app/service.py": f"class {name}: pass\n"}, CLASS_CONFIG
        )

        assert violations(root) == []

    def test_nested_local_and_conditional_definitions_are_checked(
        self, tmp_path: Path
    ) -> None:
        root = _module_project(
            tmp_path,
            {
                "app/service.py": "class Container:\n    class BaseNested: pass\ndef create():\n    class _BaseLocal: pass\nif enabled:\n    class BaseConditional: pass\n",
            },
            CLASS_CONFIG,
        )

        found = violations(root)

        assert codes_at(found) == [
            ("SMT323", "app/service.py", 2),
            ("SMT323", "app/service.py", 4),
            ("SMT323", "app/service.py", 6),
        ]
        assert [v.column for v in found] == [5, 5, 5]

    def test_imports_aliases_inheritance_functions_and_tests_are_valid(
        self, tmp_path: Path
    ) -> None:
        root = _module_project(
            tmp_path,
            {
                "app/service.py": "from pydantic import BaseModel\nfrom external import Service as BaseService\nclass Service(BaseModel): pass\nBaseAlias = Service\ndef BaseFactory(): return Service()\n",
                "tests/test_service.py": "class BaseTestService: pass\n",
            },
            CLASS_CONFIG,
        )

        assert violations(root) == []

    @pytest.mark.parametrize("selector", ["SMT323", "no-base-class-prefix"])
    def test_default_is_off_and_explicit_selection_activates_the_rule(
        self, tmp_path: Path, selector: str
    ) -> None:
        root = _module_project(
            tmp_path,
            {"app/service.py": "class BaseService: pass\n"},
            "version: 1\nproject: {root_packages: [app]}\n",
        )

        assert violations(root) == []
        assert codes_at(violations(root, CheckOptions(select=(selector,)))) == [
            ("SMT323", "app/service.py", 1)
        ]

    def test_closest_package_off_override_and_reactivation(
        self, tmp_path: Path
    ) -> None:
        config = (
            CLASS_CONFIG
            + "    compatibility:\n      match: app.compat\n      rules: {no-base-class-prefix: off}\n"
            + "    modern:\n      match: app.compat.modern\n      rules: {SMT323: error}\n"
        )
        root = _module_project(
            tmp_path,
            {
                "app/service.py": "class BaseService: pass\n",
                "app/compat/__init__.py": "class BaseLegacy: pass\n",
                "app/compat/legacy.py": "class BaseLegacy: pass\n",
                "app/compat/modern/__init__.py": "class BaseModern: pass\n",
            },
            config,
        )

        assert codes_at(violations(root)) == [
            ("SMT323", "app/compat/modern/__init__.py", 1),
            ("SMT323", "app/service.py", 1),
        ]

    def test_only_matching_packages_and_included_source_are_checked(
        self, tmp_path: Path
    ) -> None:
        config = CLASS_CONFIG.replace("match: app", "match: app.api").replace(
            "root_packages: [app]",
            "root_packages: [app]\n  exclude: [app/api/generated.py]",
        )
        root = _module_project(
            tmp_path,
            {
                "app/service.py": "class BaseService: pass\n",
                "app/api/__init__.py": "class BaseEndpoint: pass\n",
                "app/api/generated.py": "class BaseGenerated: pass\n",
            },
            config,
        )

        assert codes_at(violations(root)) == [("SMT323", "app/api/__init__.py", 1)]

    def test_global_rule_activation_needs_no_additional_policy(
        self, tmp_path: Path
    ) -> None:
        root = _module_project(
            tmp_path,
            {"app/service.py": "class BaseService: pass\n"},
            "version: 1\nproject: {root_packages: [app]}\nrules: {no-base-class-prefix: error}\n",
        )

        assert codes_at(violations(root)) == [("SMT323", "app/service.py", 1)]

    def test_explicit_off_wins_over_selection(self, tmp_path: Path) -> None:
        root = _module_project(
            tmp_path,
            {"app/service.py": "class BaseService: pass\n"},
            CLASS_CONFIG.replace(
                "no-base-class-prefix: error", "no-base-class-prefix: off"
            ),
        )

        assert violations(root, CLASS_OPTIONS) == []

    def test_conflicting_package_rules_are_rejected(self, tmp_path: Path) -> None:
        root = _module_project(
            tmp_path,
            {},
            CLASS_CONFIG
            + "    contradictory:\n      match: app\n      rules: {SMT323: off}\n",
        )

        with pytest.raises(ConfigError, match="conflicting SMT323"):
            violations(root)

    def test_cached_check_observes_renamed_classes(self, tmp_path: Path) -> None:
        root = _module_project(
            tmp_path, {"app/service.py": "class BaseService: pass\n"}, CLASS_CONFIG
        )
        assert codes_at(violations(root)) == [("SMT323", "app/service.py", 1)]

        (root / "app/service.py").write_text("class Service: pass\n", encoding="utf-8")

        assert violations(root) == violations(root, CheckOptions(use_cache=False)) == []
