from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError, discover_config, load_config, parse_config
from smelt.config.loader import load_yaml
from tests.helpers import FIXTURES

if TYPE_CHECKING:
    from pathlib import Path

MINIMAL = {"version": 1, "project": {"root_packages": ["app"]}}


def _with(**sections: object) -> dict[str, object]:
    return {**MINIMAL, **sections}


class TestDiscovery:
    def test_walks_up_from_nested_directory(self, tmp_path: Path) -> None:
        (tmp_path / "smelt.yaml").write_text("version: 1\n")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)

        assert discover_config(nested) == (tmp_path / "smelt.yaml").resolve()

    def test_accepts_yml_extension(self, tmp_path: Path) -> None:
        (tmp_path / "smelt.yml").write_text("version: 1\n")

        assert discover_config(tmp_path) == (tmp_path / "smelt.yml").resolve()

    def test_prefers_yaml_over_yml(self, tmp_path: Path) -> None:
        (tmp_path / "smelt.yml").write_text("version: 1\n")
        (tmp_path / "smelt.yaml").write_text("version: 1\n")

        assert discover_config(tmp_path) == (tmp_path / "smelt.yaml").resolve()

    def test_returns_none_without_config(self, tmp_path: Path) -> None:
        assert discover_config(tmp_path) is None


class TestParsing:
    def test_loads_gateway_fixture(self) -> None:
        loaded = load_config(FIXTURES / "gateway" / "smelt.yaml")

        domain = loaded.config.architecture.layers["domain"]
        assert domain.third_party.default == "deny"
        assert domain.third_party.allow == ["pydantic"]
        assert loaded.root == (FIXTURES / "gateway").resolve()

    def test_version_is_required(self) -> None:
        with pytest.raises(ConfigError, match="version: 1` is required"):
            parse_config({"project": {"root_packages": ["app"]}})

    def test_unknown_key_suggests_known_key(self) -> None:
        raw = _with(architecture={"layerz": {}})

        with pytest.raises(
            ConfigError,
            match=r'architecture.layerz: unknown key "layerz" \(did you mean "layers"\?\)',
        ):
            parse_config(raw)

    def test_unknown_nested_key_inside_layer(self) -> None:
        raw = _with(
            architecture={"layers": {"domain": {"path": "domain", "pathh": "x"}}}
        )

        with pytest.raises(
            ConfigError,
            match=r'architecture.layers.domain.pathh: unknown key "pathh" \(did you mean "path"\?\)',
        ):
            parse_config(raw)

    def test_severity_off_is_not_a_boolean(self) -> None:
        raw = load_yaml(
            "version: 1\nproject: {root_packages: [app]}\nrules:\n  SMT101: off\n"
        )

        config, _ = parse_config(raw)

        assert config.rules == {"SMT101": "off"}

    def test_rejects_malformed_rule_code(self) -> None:
        with pytest.raises(ConfigError, match='"layer-boundary" is not a rule code'):
            parse_config(_with(rules={"layer-boundary": "off"}))


class TestThirdPartyPolicy:
    def test_short_form_expands_to_default(self) -> None:
        raw = _with(
            architecture={
                "layers": {"domain": {"path": "domain", "third_party": "deny"}}
            }
        )

        config, _ = parse_config(raw)

        policy = config.architecture.layers["domain"].third_party
        assert (policy.default, policy.allow, policy.deny) == ("deny", [], [])

    def test_default_is_allow(self) -> None:
        config, _ = parse_config(
            _with(architecture={"layers": {"domain": {"path": "domain"}}})
        )

        assert config.architecture.layers["domain"].third_party.default == "allow"

    def test_allow_list_requires_default_deny(self) -> None:
        raw = _with(
            architecture={
                "layers": {
                    "domain": {"path": "domain", "third_party": {"allow": ["x"]}}
                }
            }
        )

        with pytest.raises(
            ConfigError, match="`allow` is only valid with `default: deny`"
        ):
            parse_config(raw)

    def test_deny_list_requires_default_allow(self) -> None:
        raw = _with(
            architecture={
                "layers": {
                    "domain": {
                        "path": "domain",
                        "third_party": {"default": "deny", "deny": ["x"]},
                    }
                }
            }
        )

        with pytest.raises(
            ConfigError, match="`deny` is only valid with `default: allow`"
        ):
            parse_config(raw)


class TestFeatures:
    def test_requires_exactly_one_of_root_or_pattern(self) -> None:
        raw = _with(
            architecture={
                "features": {"root": "app.features", "pattern": "app.{feature}"}
            }
        )

        with pytest.raises(ConfigError, match="exactly one of `root` or `pattern`"):
            parse_config(raw)

    def test_pattern_needs_placeholder(self) -> None:
        with pytest.raises(
            ConfigError, match='must end with a "\\{feature\\}" segment'
        ):
            parse_config(_with(architecture={"features": {"pattern": "app.features"}}))
