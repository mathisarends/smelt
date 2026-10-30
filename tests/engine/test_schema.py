from smelt.engine.schema import config_json_schema
from smelt.rules.registry import build_rule_set


def test_editor_rule_completion_uses_the_runtime_catalog_and_examples() -> None:
    schema = config_json_schema()
    settings = schema["$defs"]["RuleSettings"]

    assert set(settings["properties"]) == {rule.name for rule in build_rule_set().rules}
    assert settings["additionalProperties"] is False
    for rule in build_rule_set().rules:
        entry = settings["properties"][rule.name]
        assert entry["enum"] == ["error", "warning", "hint", "off"]
        assert rule.code in entry["title"]
        assert rule.explain().bad in entry["markdownDescription"]
        assert rule.explain().good in entry["markdownDescription"]
        assert settings["patternProperties"][f"^{rule.code}$"] == entry
    assert schema["$defs"]["PackageContract"]["properties"]["rules"] == {
        "$ref": "#/$defs/SourceRuleSettings"
    }


def test_editor_schema_does_not_advertise_removed_switches() -> None:
    schema = config_json_schema()

    assert "preset" not in schema["properties"]
    tests = schema["$defs"]["TestsConfig"]["properties"]
    assert not any(
        key.startswith("review_") or key.startswith("require_") for key in tests
    )
    assert "enabled" not in schema["$defs"]["OrderingConfig"]["properties"]
    contract = schema["$defs"]["PackageContract"]["properties"]
    assert (
        not {"member_visibility", "static_methods", "instance_assignments"}
        & contract.keys()
    )


def test_package_completion_and_runtime_rule_scope_agree() -> None:
    schema = config_json_schema()
    source = schema["$defs"]["SourceRuleSettings"]["properties"]

    assert set(source) == {
        rule.name
        for rule in build_rule_set().rules
        if getattr(rule, "scope", "source") == "source"
    }
    assert "test-only-public-attribute" in source
    assert "unbounded-test-wait" not in source
    assert "incomplete-coverage" not in source
