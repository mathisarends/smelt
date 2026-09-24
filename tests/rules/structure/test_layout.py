from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import LAYERED_CONFIG, codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

FEATURE_CONFIG = """
version: 1
project:
  root_packages: [gw]
architecture:
  features:
    root: gw.features
  shared: [gw.shared]
  composition_root: [gw.bootstrap]
  layers:
    domain: {path: domain}
    infrastructure: {path: infra.adapters}
"""


def _features(tmp_path: Path, files: dict[str, str]) -> Path:
    base = {
        "smelt.yaml": FEATURE_CONFIG,
        "gw/__init__.py": "",
        "gw/bootstrap.py": "",
        "gw/shared/__init__.py": "",
        "gw/shared/clock.py": "",
        "gw/features/__init__.py": "",
        "gw/features/voice/__init__.py": "",
        "gw/features/voice/domain/__init__.py": "",
        "gw/features/voice/domain/call.py": "",
    }
    return write_project(tmp_path, {**base, **files})


class TestUnknownLayer:
    def test_declared_layers_are_fine(self, tmp_path: Path) -> None:
        root = _features(
            tmp_path,
            {
                "gw/features/voice/infra/__init__.py": "",
                "gw/features/voice/infra/adapters/__init__.py": "",
                "gw/features/voice/infra/adapters/sql.py": "",
                "gw/features/voice/domain/values/__init__.py": "",
            },
        )

        assert violations(root, CheckOptions(select=("SMT301",))) == []

    def test_undeclared_package_in_feature(self, tmp_path: Path) -> None:
        root = _features(tmp_path, {"gw/features/voice/models/__init__.py": ""})

        [found] = violations(root, CheckOptions(select=("SMT301",)))

        assert found.path == "gw/features/voice/models/__init__.py"
        assert found.message == (
            "voice contains package models, which is not a declared layer "
            "(domain, infrastructure)"
        )

    def test_undeclared_package_below_dotted_layer_prefix(self, tmp_path: Path) -> None:
        root = _features(
            tmp_path,
            {
                "gw/features/voice/infra/__init__.py": "",
                "gw/features/voice/infra/queues/__init__.py": "",
                "gw/features/voice/infra/queues/kafka.py": "",
            },
        )

        found = violations(root, CheckOptions(select=("SMT301",)))

        assert [v.source_module for v in found] == ["gw.features.voice.infra.queues"]

    def test_namespace_package_without_init(self, tmp_path: Path) -> None:
        root = _features(tmp_path, {"gw/features/voice/misc/thing.py": ""})

        [found] = violations(root, CheckOptions(select=("SMT301",)))

        assert found.path == "gw/features/voice/misc"

    def test_root_package_without_features(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": LAYERED_CONFIG,
                "app/__init__.py": "",
                "app/domain/__init__.py": "",
                "app/cli/__init__.py": "",
            },
        )

        found = violations(root, CheckOptions(select=("SMT301",)))

        assert [v.source_module for v in found] == ["app.cli"]

    def test_loose_module_in_feature(self, tmp_path: Path) -> None:
        root = _features(tmp_path, {"gw/features/voice/helpers.py": ""})

        [found] = violations(root, CheckOptions(select=("SMT301",)))

        assert found.path == "gw/features/voice/helpers.py"
        assert found.message == (
            "voice contains module helpers.py, which is in no layer "
            "(domain, infrastructure)"
        )
        assert found.hint == (
            "Move voice/helpers.py into a layer of voice, such as infrastructure "
            "for configuration and external services."
        )

    def test_loose_module_below_dotted_layer_prefix(self, tmp_path: Path) -> None:
        root = _features(
            tmp_path,
            {
                "gw/features/voice/infra/__init__.py": "",
                "gw/features/voice/infra/config.py": "",
                "gw/features/voice/infra/adapters/__init__.py": "",
                "gw/features/voice/infra/adapters/sql.py": "",
            },
        )

        found = violations(root, CheckOptions(select=("SMT301",)))

        assert [v.source_module for v in found] == ["gw.features.voice.infra.config"]

    def test_modules_outside_features_are_not_loose(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": LAYERED_CONFIG,
                "app/__init__.py": "",
                "app/main.py": "",
                "app/domain/__init__.py": "",
            },
        )

        assert violations(root, CheckOptions(select=("SMT301",))) == []


class TestCrowdedPackage:
    def test_reports_packages_above_threshold_as_hint(self, tmp_path: Path) -> None:
        files = {f"gw/features/voice/domain/m{i}.py": "" for i in range(3)}
        config = FEATURE_CONFIG + "structure:\n  crowded_threshold: 3\n"
        root = _features(tmp_path, {**files, "smelt.yaml": config})

        [found] = violations(root, CheckOptions(select=("SMT304",)))

        assert found.severity is Severity.HINT
        assert found.message == (
            "voice/domain has 4 modules; consider grouping related modules"
        )


class TestUnclassifiedModule:
    def test_only_modules_outside_the_model(self, tmp_path: Path) -> None:
        root = _features(tmp_path, {"gw/settings.py": ""})

        found = violations(root, CheckOptions(select=("SMT305",)))

        assert codes_at(found) == [("SMT305", "gw/settings.py", None)]

    def test_silent_without_layers_or_features(self, tmp_path: Path) -> None:
        root = write_project(
            tmp_path,
            {
                "smelt.yaml": "version: 1\nproject:\n  root_packages: [app]\n",
                "app/__init__.py": "",
                "app/main.py": "",
            },
        )

        assert violations(root, CheckOptions(select=("SMT305",))) == []
