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


@pytest.mark.parametrize(
    "name", ["MessageBase", "_MessageBase", "__MessageBase", "RepositoryBase"]
)
def test_base_suffix_definitions_are_reported_without_renaming(
    tmp_path: Path, name: str
) -> None:
    source = f"class {name}: pass\n"
    root = _module_project(tmp_path, {"app/service.py": source}, CLASS_CONFIG)
    [finding] = violations(root, CheckOptions(select=("SMT326",)))
    assert codes_at([finding]) == [("SMT326", "app/service.py", 1)]
    assert finding.expected == {
        "forbidden_suffix": "Base",
        "class": name,
        "automatic_rename": False,
    }
    assert (root / "app/service.py").read_text() == source


def test_suffix_convention_is_opt_in_and_preserves_imports_and_nearby_names(
    tmp_path: Path,
) -> None:
    root = _module_project(
        tmp_path,
        {
            "app/service.py": "from external import MessageBase\nclass Message(MessageBase): pass\nclass Database: pass\nclass Base64: pass\n"
        },
        CLASS_CONFIG,
    )
    assert violations(root, CheckOptions(select=("SMT326",))) == []
    write_project(root, {"app/service.py": "class MessageBase: pass\n"})
    assert violations(root) == []
    write_project(
        root, {"smelt.yml": CLASS_CONFIG + "\nrules: {no-base-class-suffix: error}\n"}
    )
    assert [v.code for v in violations(root)] == ["SMT326"]


def test_suffix_package_override_and_suppression(tmp_path: Path) -> None:
    config = CLASS_CONFIG.replace(
        "no-base-class-prefix: error", "no-base-class-suffix: off"
    )
    root = _module_project(
        tmp_path, {"app/service.py": "class MessageBase: pass\n"}, config
    )
    assert violations(root, CheckOptions(select=("SMT326",))) == []
    write_project(
        root,
        {
            "smelt.yml": CLASS_CONFIG,
            "app/service.py": "class MessageBase:  # smelt: ignore[SMT326] -- Published plugin abstraction name.\n    pass\n",
        },
    )
    assert violations(root, CheckOptions(select=("SMT326",))) == []


def test_suffix_checks_nested_and_local_definitions_but_not_tests(
    tmp_path: Path,
) -> None:
    root = _module_project(
        tmp_path,
        {
            "app/service.py": "class Container:\n    class MessageBase: pass\ndef create():\n    class LocalBase: pass\n",
            "tests/test_service.py": "class TestBase: pass\n",
        },
        CLASS_CONFIG,
    )
    assert codes_at(violations(root, CheckOptions(select=("SMT326",)))) == [
        ("SMT326", "app/service.py", 2),
        ("SMT326", "app/service.py", 4),
    ]


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


REDUNDANT_CONFIG = """version: 1
project: {root_packages: [backend]}
rules: {redundant-module-name: hint}
"""
REDUNDANT_PATH = "backend/features/cars/application/car_exporter.py"


class TestRedundantModuleName:
    def test_issue_94_example_and_valid_neighbor(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": REDUNDANT_CONFIG,
                REDUNDANT_PATH: "",
                "backend/features/cars/application/importer.py": "",
            },
        )

        [finding] = violations(root)

        assert codes_at([finding]) == [("SMT336", REDUNDANT_PATH, None)]
        assert finding.source_module == "backend.features.cars.application.car_exporter"
        assert finding.severity.value == "hint"
        assert finding.expected == {
            "ancestor": "backend.features.cars",
            "repeated_name": "car",
            "suggested_name": "exporter.py",
            "automatic_rename": False,
        }
        assert (root / REDUNDANT_PATH).is_file()
        assert not (root / REDUNDANT_PATH).with_name("exporter.py").exists()

    @pytest.mark.parametrize(
        ("package", "filename", "suggested"),
        [
            ("cars", "cars_exporter", "exporter.py"),
            ("cars", "exporter_car", "exporter.py"),
            ("cars", "exporter_cars", "exporter.py"),
            ("cars", "_car_exporter", "_exporter.py"),
            ("purchase_orders", "purchase_order_exporter", "exporter.py"),
            ("purchase_orders", "purchase_orders_exporter", "exporter.py"),
            ("status", "status_report", "report.py"),
            ("_cars", "car_exporter", "exporter.py"),
        ],
    )
    def test_whole_ancestor_names_at_either_end(
        self, tmp_path: Path, package: str, filename: str, suggested: str
    ) -> None:
        path = f"backend/{package}/{filename}.py"
        root = write_project(tmp_path, {"smelt.yml": REDUNDANT_CONFIG, path: ""})

        [finding] = violations(root)

        assert codes_at([finding]) == [("SMT336", path, None)]
        assert finding.expected is not None
        assert finding.expected["suggested_name"] == suggested

    @pytest.mark.parametrize(
        "path",
        [
            "backend/cars/cartridge.py",
            "backend/cars/cargo_exporter.py",
            "backend/cars/electric_car_exporter.py",
            "backend/cars/car.py",
            "backend/cars/cars.py",
            "backend/cars/car_class.py",
            "backend/cars/car_123.py",
            "backend/people/person_exporter.py",
            "backend/status/statu_exporter.py",
            "backend/analysis/analysi_exporter.py",
            "backend/classes/classe_exporter.py",
            "backend/categories/categorie_exporter.py",
            "backend/cars/car_exporter/__init__.py",
            "backend/main/__main__.py",
            "tests/cars/test_car_exporter.py",
        ],
    )
    def test_valid_neighbors_and_out_of_scope_names(
        self, tmp_path: Path, path: str
    ) -> None:
        root = write_project(
            tmp_path,
            {"smelt.yml": REDUNDANT_CONFIG, "backend/__init__.py": "", path: ""},
        )

        assert violations(root) == []

    @pytest.mark.parametrize("selector", ["SMT336", "redundant-module-name", "SMT33"])
    def test_opt_in_selection_without_architecture_policy(
        self, tmp_path: Path, selector: str
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": REDUNDANT_CONFIG.split("rules:", maxsplit=1)[0],
                REDUNDANT_PATH: "",
            },
        )

        assert violations(root) == []
        assert codes_at(violations(root, CheckOptions(select=(selector,)))) == [
            ("SMT336", REDUNDANT_PATH, None)
        ]
        assert (
            violations(root, CheckOptions(select=(selector,), ignore=("SMT336",))) == []
        )

    def test_closest_package_override_and_file_suppression(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": REDUNDANT_CONFIG
                + """structure:
  contracts:
    compatibility:
      match: backend.features.cars
      rules: {redundant-module-name: off}
    modern:
      match: backend.features.cars.application
      rules: {redundant-module-name: warning}
""",
                REDUNDANT_PATH: "",
                "backend/features/cars/car_legacy.py": "",
                "backend/features/cars/application/car_api.py": "# smelt: ignore-file[SMT336] -- Published module path.\n",
            },
        )

        [finding] = violations(root, CheckOptions(select=("SMT336",)))

        assert finding.path == REDUNDANT_PATH
        assert finding.severity.value == "warning"

    def test_source_roots_and_excluded_files_are_not_context(
        self, tmp_path: Path
    ) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yml": """version: 1
project:
  root_packages: [backend]
  source_roots: [cars]
  exclude: [cars/backend/cars/car_generated.py]
rules: {redundant-module-name: hint}
""",
                "cars/backend/car_exporter.py": "",
                "cars/backend/cars/car_generated.py": "",
            },
        )

        assert violations(root) == []

    @pytest.mark.parametrize("occupied", ["exporter.py", "exporter/__init__.py"])
    def test_collisions_and_renames_invalidate_cached_findings(
        self, tmp_path: Path, occupied: str
    ) -> None:
        root = write_project(
            tmp_path,
            {"smelt.yml": REDUNDANT_CONFIG, REDUNDANT_PATH: ""},
        )
        [initial] = violations(root)
        assert initial.expected is not None
        assert initial.expected["suggested_name"] == "exporter.py"

        sibling = str((root / REDUNDANT_PATH).parent.relative_to(root) / occupied)
        write_project(root, {sibling: ""})

        cached = violations(root)
        assert cached == violations(root, CheckOptions(use_cache=False))
        assert len(cached) == 1
        assert cached[0].expected is not None
        assert cached[0].expected["suggested_name"] is None

        (root / sibling).unlink()
        (root / REDUNDANT_PATH).rename((root / REDUNDANT_PATH).with_name("exporter.py"))

        assert violations(root) == violations(root, CheckOptions(use_cache=False)) == []
