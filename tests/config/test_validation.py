import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from smelt.config import ConfigError, errors, parse_config
from smelt.config.errors import UPGRADE_GUIDE_URL

MINIMAL = {"version": 2, "project": {"root_packages": ["gateway"]}}


@pytest.mark.parametrize(
    ("old", "successor"),
    [
        (
            {"structure": {"private_modules": "forbid"}},
            "style and architecture.packages",
        ),
        (
            {"architecture": {"imports": {"transitive": True}}},
            "architecture.import_graph",
        ),
        ({"tests": {"unmirrored": ["legacy/**"]}}, "mirror_exclude"),
    ],
)
def test_retired_config_keys_point_to_migration(
    old: dict[str, object], successor: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        parse_config({**MINIMAL, **old})

    assert successor in str(caught.value)
    assert f"see {UPGRADE_GUIDE_URL}" in str(caught.value)


def _package_policy(spec: dict[str, object]) -> dict[str, object]:
    if spec.keys() & {
        "public",
        "required",
        "min_imports_to_bundle",
        "min_importers_to_export",
    }:
        return {**MINIMAL, "architecture": {"packages": {"library": spec}}}
    return {**MINIMAL, "style": {"overrides": {"library": spec}}}


class TestPackageContracts:
    @pytest.mark.parametrize(
        "spec",
        [
            {"match": "gateway.*", "required": ["api", "core.models"]},
            {"match": "gateway", "public": ["."]},
            {"match": "gateway", "public": []},
            {"match": "*.*", "public": ["api.**", "commands.*"]},
            {"match": "gateway", "imports": {"style": "absolute"}},
            {
                "match": "gateway",
                "imports": {"style": "relative", "max_relative_level": 2},
            },
            {"match": "gateway", "min_imports_to_bundle": 3},
            {"match": "gateway", "module_docstrings": "forbid"},
            {"match": "gateway", "future_annotations": "when-needed"},
            {"match": "gateway", "private_modules": "forbid"},
            {"match": "gateway", "private_modules": "allow"},
            {
                "match": "gateway",
                "ordering": {},
                "rules": {"layout/definition-order": "error"},
            },
            {
                "match": "gateway",
                "rules": {"idioms/bundled-instance-assignment": "error"},
            },
            {
                "match": "gateway",
                "rules": {"idioms/bundled-instance-assignment": "off"},
            },
            {"match": "gateway", "type_dispatch": {}},
            {"match": "gateway", "type_dispatch": {"style": "allow"}},
            {"match": "gateway", "bases": {"pydantic.BaseModel": "gateway.Model"}},
            {"match": "gateway", "bases": {}},
            {"match": "gateway", "errors": {"base": "gateway.errors.GatewayError"}},
            {"match": "gateway", "errors": {"base": "app.E", "allow": ["TypeError"]}},
            {"match": "gateway", "errors": {}},
            {
                "match": "gateway",
                "ordering": {},
                "rules": {"layout/definition-order": "off"},
            },
        ],
    )
    def test_valid_contracts(self, spec: dict[str, object]) -> None:
        config, _ = parse_config(_package_policy(spec))

        assert "library" in config.structure.contracts

    @pytest.mark.parametrize(
        "spec",
        [
            {"match": "gateway"},
            {"match": "gateway.**", "public": []},
            {"match": "gateway.prefix*", "public": []},
            {"match": "gateway", "required": ["../api"]},
            {"match": "gateway", "required": ["api.py/"]},
            {"match": "gateway", "required": ["api.*"]},
            {"match": "gateway", "public": ["api.*impl"]},
            {"match": "gateway", "public": [""]},
            {"match": "gateway", "public": ["/api"]},
            {"match": "other", "public": []},
            {"match": "gateway", "imports": {"style": "sometimes"}},
            {
                "match": "gateway",
                "rules": {"idioms/bundled-instance-assignment": "sometimes"},
            },
            {"match": "gateway", "type_dispatch": {"style": "sometimes"}},
            {"match": "gateway", "type_dispatch": {"min_branches": 1}},
            {"match": "gateway", "imports": {"max_relative_level": 0}},
            {"match": "gateway", "min_imports_to_bundle": 1},
            {"match": "gateway", "module_docstrings": "sometimes"},
            {"match": "gateway", "future_annotations": "always"},
            {"match": "gateway", "private_modules": "sometimes"},
            {"match": "gateway", "bases": {"BaseModel": "gateway.Model"}},
            {"match": "gateway", "bases": {"pydantic.BaseModel": "gateway..Model"}},
            {"match": "gateway", "bases": {"gateway.Model": "gateway.Model"}},
            {"match": "gateway", "errors": {"base": "GatewayError"}},
            {"match": "gateway", "errors": {"base": "app..Error"}},
            {"match": "gateway", "errors": {"base": "app.E", "allow": ["Nope"]}},
            {"match": "gateway", "errors": {"base": "app.E", "allow": ["len"]}},
            {"match": "gateway", "errors": {"base": "app.E", "raise": "x"}},
            {
                "match": "gateway",
                "ordering": {"module": ["public", "private"]},
                "rules": {"layout/definition-order": "error"},
            },
            {
                "match": "gateway",
                "ordering": {"class": ["init", "public", "private"]},
                "rules": {"layout/definition-order": "error"},
            },
            {
                "match": "gateway",
                "ordering": {"fix": "unsafe"},
                "rules": {"layout/definition-order": "error"},
            },
        ],
    )
    def test_invalid_contracts_fail_explicitly(self, spec: dict[str, object]) -> None:
        with pytest.raises(
            ConfigError, match=r"(?:style\.overrides|architecture\.packages)\.library"
        ):
            parse_config(_package_policy(spec))

    def test_contract_names_must_not_be_empty(self) -> None:
        with pytest.raises(ConfigError, match="package policy names must not be empty"):
            parse_config(
                {
                    **MINIMAL,
                    "architecture": {
                        "packages": {" ": {"match": "gateway", "public": []}}
                    },
                }
            )


def _layers(**layers: dict[str, object]) -> dict[str, object]:
    return {**MINIMAL, "architecture": {"layers": layers}}


class TestLayerReferences:
    def test_unknown_dependency_suggests_prefix_match(self) -> None:
        raw = _layers(
            infrastructure={"path": "infra"},
            application={"path": "application", "may_depend_on": ["domain", "infra"]},
            domain={"path": "domain"},
        )

        with pytest.raises(ConfigError) as caught:
            parse_config(raw)

        assert str(caught.value.issues[0]) == (
            'architecture.layers.application.may_depend_on[1]: unknown layer "infra" '
            '(did you mean "infrastructure"?)'
        )

    def test_duplicate_layer_paths(self) -> None:
        raw = _layers(a={"path": "core"}, b={"path": "core"})

        with pytest.raises(ConfigError, match='duplicate layer path "core"'):
            parse_config(raw)

    def test_cyclic_dependencies_only_warn(self) -> None:
        raw = _layers(
            a={"path": "a", "may_depend_on": ["b"]},
            b={"path": "b", "may_depend_on": ["a"]},
        )

        _, warnings = parse_config(raw)

        assert [str(w) for w in warnings] == [
            "architecture.layers: cyclic may_depend_on: a -> b -> a"
        ]

    def test_cross_feature_pair_with_unknown_layer(self) -> None:
        raw = {
            **MINIMAL,
            "architecture": {
                "layers": {"application": {"path": "application"}},
                "cross_feature": {"allow": ["application -> domain"]},
            },
        }

        with pytest.raises(
            ConfigError, match=r'cross_feature\.allow\[0\]: unknown layer "domain"'
        ):
            parse_config(raw)

    def test_scoped_cross_feature_allowance_checks_layers(self) -> None:
        raw = {
            **MINIMAL,
            "architecture": {
                "layers": {"application": {"path": "application"}},
                "cross_feature": {
                    "allow": [{"from": "billing.application", "to": "voice.domian"}]
                },
            },
        }

        with pytest.raises(ConfigError, match='unknown layer "domian"'):
            parse_config(raw)


class TestModuleSets:
    def test_shared_must_be_inside_root_packages(self) -> None:
        raw = {**MINIMAL, "architecture": {"shared": ["other.shared"]}}

        with pytest.raises(
            ConfigError, match=r'"other\.shared" is not inside project\.root_packages'
        ):
            parse_config(raw)

    def test_shared_must_not_overlap_features_root(self) -> None:
        raw = {
            **MINIMAL,
            "architecture": {
                "features": {"root": "gateway.features"},
                "shared": ["gateway.features.common"],
            },
        }

        with pytest.raises(ConfigError, match="is inside the features root"):
            parse_config(raw)


class TestCentralModules:
    def test_inside_features_root(self) -> None:
        raw = {
            **MINIMAL,
            "architecture": {
                "features": {"root": "gateway.features"},
                "layers": {
                    "domain": {"path": "domain", "modules": ["gateway.features.voice"]}
                },
            },
        }

        with pytest.raises(ConfigError, match="overlaps the features root"):
            parse_config(raw)


@pytest.mark.parametrize(
    "policy",
    [
        {"quality_exclude": [""]},
        {"forbidden_mock_paths": ["../other/tests"]},
        {"forbidden_mock_paths": ["C:/tests/**"]},
        {"quality_exclude": ["/tests/**"]},
        {"assertion_helpers": ["not a callable"]},
    ],
)
def test_invalid_test_quality_policy_is_rejected(policy: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_config({**MINIMAL, "tests": policy})


def test_test_quality_globs_are_canonical() -> None:
    config, _ = parse_config(
        {
            **MINIMAL,
            "tests": {
                "forbidden_mock_paths": [
                    "./tests\\**\\domain\\**",
                    "tests/**/domain/**",
                ],
            },
        }
    )
    assert config.tests.forbidden_mock_paths == ["tests/**/domain/**"]


@pytest.mark.parametrize(
    ("version", "checkout", "ref"),
    [
        ("0.13.0", False, "v0.13.0"),
        ("0.13.0", True, "main"),
        ("0.14.0.dev1", False, "main"),
        ("0.0.0", False, "main"),
    ],
)
def test_migration_guide_tracks_release_builds(
    tmp_path: Path, version: str, checkout: bool, *, ref: str
) -> None:
    package = tmp_path / "smelt"
    (package / "config").mkdir(parents=True)
    (package / "__init__.py").write_text(f"__version__ = {version!r}", encoding="utf-8")
    shutil.copyfile(errors.__file__, package / "config/errors.py")
    if checkout:
        (tmp_path / ".git").mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            "from smelt.config.errors import UPGRADE_GUIDE_URL; print(UPGRADE_GUIDE_URL)",
        ],
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert f"/blob/{ref}/" in result.stdout
