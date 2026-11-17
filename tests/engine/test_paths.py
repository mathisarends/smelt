from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from tests.helpers import MEMBER_ARCHITECTURES, MEMBER_BOUNDARIES, check, write_project

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


def test_conflicting_type_dispatch_policies_are_rejected(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\nstructure:\n  contracts:\n    first: {match: app, type_dispatch: {min_branches: 3}}\n    second: {match: app, type_dispatch: {min_branches: 4}}",
            "app/__init__.py": "",
        },
    )
    with pytest.raises(ConfigError, match="conflicting type dispatch policies"):
        check(tmp_path)


def test_conflicting_instance_assignment_policies_are_rejected(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject:\n  root_packages:\n  - app\nstructure:\n  contracts:\n    first:\n      match: app\n      rules:\n        idioms/bundled-instance-assignment: error\n    second:\n      match: app\n      rules:\n        idioms/bundled-instance-assignment: 'off'\n",
            "app/__init__.py": "",
        },
    )
    with pytest.raises(ConfigError, match="conflicting SMT314 settings"):
        check(tmp_path)


def _issues(tmp_path: Path, architecture: str, tests: str = "") -> list[str]:
    config = (
        "version: 1\n"
        "project: {root_packages: [app]}\n"
        f"architecture:\n{architecture}"
        "  layers: {domain: {path: domain}}\n"
        f"{tests}"
    )
    write_project(tmp_path, {**SOURCES, "smelt.yml": config})
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
                "smelt.yml": (
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
                "smelt.yml": (
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


def test_a_missing_member_module_names_the_member_file(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            **MEMBER_ARCHITECTURES,
            "services/api/smelt.yml": "architecture: {features: {root: api.features}, shared: [api.common]}\n",
        },
    )

    with pytest.raises(
        ConfigError, match=r'module "api\.common" does not exist'
    ) as caught:
        check(tmp_path)

    issue = caught.value.issues[0]
    assert issue.path == "architecture.shared[0]"
    assert (issue.source or "").endswith("services/api/smelt.yml")


@pytest.mark.parametrize(
    ("members", "location", "pattern"),
    [
        (
            "{'service/*': {may_depend_on: []}}",
            "architecture.members.service/*",
            "service/*",
        ),
        (
            "{'services/*': {may_depend_on: [packages/nope]}}",
            "architecture.members.services/*.may_depend_on[0]",
            "packages/nope",
        ),
    ],
)
def test_a_member_boundary_glob_must_match_a_member(
    tmp_path: Path, members: str, location: str, pattern: str
) -> None:
    write_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": "version: 1\nproject: {workspace: uv, source_roots: [src]}\n"
            f"architecture: {{members: {members}}}\n",
        },
    )

    with pytest.raises(ConfigError, match="matches no workspace member") as caught:
        check(tmp_path)

    assert [
        (issue.path, issue.message.split(" matches")[0])
        for issue in caught.value.issues
    ] == [(location, f'"{pattern}"')]


def test_the_workspace_root_is_a_member_boundary_target(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": "version: 1\nproject: {workspace: uv, source_roots: [src]}\n"
            "architecture: {members: {'.': {may_depend_on: []}, 'packages/*': {may_depend_on: ['.']}}}\n",
        },
    )

    assert check(tmp_path).report.violations == []
