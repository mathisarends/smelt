import pytest

from smelt.config import ConfigError, parse_config

MINIMAL = {"version": 1, "project": {"root_packages": ["gateway"]}}


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
            {"match": "gateway", "bundle_imports_at": 3},
            {"match": "gateway", "module_docstrings": "forbid"},
            {"match": "gateway", "future_annotations": "when-needed"},
            {"match": "gateway", "private_modules": "forbid"},
            {"match": "gateway", "private_modules": "allow"},
            {"match": "gateway", "ordering": {}},
            {"match": "gateway", "ordering": {"enabled": False}},
        ],
    )
    def test_valid_contracts(self, spec: dict[str, object]) -> None:
        config, _ = parse_config(
            {**MINIMAL, "structure": {"contracts": {"library": spec}}}
        )

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
            {"match": "gateway", "imports": {"max_relative_level": 0}},
            {"match": "gateway", "bundle_imports_at": 1},
            {"match": "gateway", "module_docstrings": "sometimes"},
            {"match": "gateway", "future_annotations": "always"},
            {"match": "gateway", "private_modules": "sometimes"},
            {"match": "gateway", "ordering": {"module": ["public", "private"]}},
            {"match": "gateway", "ordering": {"class": ["init", "public", "private"]}},
            {"match": "gateway", "ordering": {"fix": "unsafe"}},
        ],
    )
    def test_invalid_contracts_fail_explicitly(self, spec: dict[str, object]) -> None:
        with pytest.raises(ConfigError, match=r"structure\.contracts\.library"):
            parse_config({**MINIMAL, "structure": {"contracts": {"library": spec}}})

    def test_contract_names_must_not_be_empty(self) -> None:
        with pytest.raises(ConfigError, match="contract names must not be empty"):
            parse_config(
                {
                    **MINIMAL,
                    "structure": {
                        "contracts": {" ": {"match": "gateway", "public": []}}
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
    def test_unknown_layer(self) -> None:
        raw = {
            **MINIMAL,
            "architecture": {
                "layers": {"infrastructure": {"path": "infrastructure"}},
                "modules": {"gateway.platform": "infra"},
            },
        }

        with pytest.raises(ConfigError) as caught:
            parse_config(raw)

        assert str(caught.value.issues[0]) == (
            'architecture.modules.gateway.platform: unknown layer "infra" '
            '(did you mean "infrastructure"?)'
        )

    def test_inside_features_root(self) -> None:
        raw = {
            **MINIMAL,
            "architecture": {
                "features": {"root": "gateway.features"},
                "layers": {"domain": {"path": "domain"}},
                "modules": {"gateway.features.voice": "domain"},
            },
        }

        with pytest.raises(ConfigError, match="overlaps the features root"):
            parse_config(raw)
