from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

SOURCES = {
    "app/__init__.py": "",
    "app/features/__init__.py": "",
    "app/features/billing/__init__.py": "",
    "app/features/billing/domain/__init__.py": "",
    "app/shared/__init__.py": "",
    "app/main.py": "",
}


def _issues(tmp_path: Path, architecture: str, tests: str = "") -> list[str]:
    config = (
        "version: 1\n"
        "project: {root_packages: [app]}\n"
        f"architecture:\n{architecture}"
        "  layers: {domain: {path: domain}}\n"
        f"{tests}"
    )
    write_project(tmp_path, {**SOURCES, "smelt.yaml": config})
    with pytest.raises(ConfigError) as caught:
        check(tmp_path)
    return [str(issue) for issue in caught.value.issues]


class TestMissingPaths:
    def test_unknown_feature_in_specific_allowance(self, tmp_path: Path) -> None:
        issues = _issues(
            tmp_path,
            "  features: {root: app.features}\n"
            "  cross_feature: {allow: [{from: billng.domain, to: billing.domain}]}\n",
        )

        assert issues == [
            'architecture.cross_feature.allow[0].from: unknown feature "billng" '
            '(did you mean "billing"?)'
        ]

    def test_mistyped_features_root(self, tmp_path: Path) -> None:
        issues = _issues(tmp_path, "  features: {root: app.featurez}\n")

        assert issues == [
            'architecture.features.root: package "app.featurez" does not exist '
            '(did you mean "app.features"?)'
        ]

    def test_missing_shared_and_composition_root(self, tmp_path: Path) -> None:
        issues = _issues(
            tmp_path,
            "  shared: [app.shard]\n  composition_root: [app.bootstrap]\n",
        )

        assert issues == [
            'architecture.shared[0]: module "app.shard" does not exist '
            '(did you mean "app.shared"?)',
            'architecture.composition_root[0]: module "app.bootstrap" does not exist',
        ]

    def test_wiring_pattern_without_match(self, tmp_path: Path) -> None:
        issues = _issues(tmp_path, "  wiring: [app.features.*.infrastructure.di]\n")

        assert issues == [
            'architecture.wiring[0]: "app.features.*.infrastructure.di" matches no module'
        ]

    def test_missing_test_root_with_mirror_layout(self, tmp_path: Path) -> None:
        issues = _issues(
            tmp_path,
            "  shared: [app.shared]\n",
            "tests: {layout: mirror}\n",
        )

        assert issues == ['project.test_roots[0]: directory "tests" does not exist']

    def test_missing_source_root(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                **SOURCES,
                "smelt.yaml": (
                    "version: 1\n"
                    "project: {root_packages: [app], source_roots: [src, .]}\n"
                ),
            },
        )

        with pytest.raises(ConfigError) as caught:
            check(tmp_path)

        assert [str(issue) for issue in caught.value.issues] == [
            'project.source_roots[0]: directory "src" does not exist'
        ]

    def test_valid_config_passes(self, tmp_path: Path) -> None:
        write_project(
            tmp_path,
            {
                **SOURCES,
                "tests/__init__.py": "",
                "smelt.yaml": (
                    "version: 1\n"
                    "project: {root_packages: [app]}\n"
                    "architecture:\n"
                    "  features: {root: app.features}\n"
                    "  shared: [app.shared]\n"
                    "  composition_root: [app.main]\n"
                    "  layers: {domain: {path: domain}}\n"
                    "tests: {layout: mirror}\n"
                ),
            },
        )

        assert check(tmp_path).report.violations == []
