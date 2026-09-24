import pytest

from smelt.config import ConfigError, parse_config

MINIMAL = {"version": 1, "project": {"root_packages": ["gateway"]}}


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


class TestRoles:
    def test_implements_unknown_role(self) -> None:
        raw = {
            **MINIMAL,
            "roles": {
                "port": {"detect": {"base": "typing.Protocol"}},
                "adapter": {"detect": {"implements": "prot"}},
            },
        }

        with pytest.raises(
            ConfigError, match='unknown role "prot" \\(did you mean "port"\\?\\)'
        ):
            parse_config(raw)

    def test_detect_needs_exactly_one_condition(self) -> None:
        raw = {**MINIMAL, "roles": {"port": {"detect": {}}}}

        with pytest.raises(ConfigError, match="exactly one condition"):
            parse_config(raw)


class TestTestsSection:
    def test_unknown_patching_category(self) -> None:
        raw = {**MINIMAL, "tests": {"patching": {"forbid": ["privat"]}}}

        with pytest.raises(
            ConfigError,
            match='unknown category "privat" \\(did you mean "private"\\?\\)',
        ):
            parse_config(raw)

    def test_feature_layout_requires_features(self) -> None:
        with pytest.raises(
            ConfigError, match=r'"feature" layout requires architecture\.features'
        ):
            parse_config({**MINIMAL, "tests": {"layout": "feature"}})
