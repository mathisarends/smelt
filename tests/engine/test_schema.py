from smelt.diagnostics.violation import Category
from smelt.engine.schema import config_json_schema
from smelt.rules.registry import build_rule_set


def test_editor_schema_accepts_local_extends_and_policy_fragments() -> None:
    schema = config_json_schema()

    assert schema["properties"]["extends"]["type"] == "string"
    assert schema["properties"]["extends"]["minLength"] == 1
    assert "required" not in schema
    assert "required" not in schema["$defs"]["LayerConfig"]


def test_editor_rule_completion_uses_the_runtime_catalog_and_examples() -> None:
    schema = config_json_schema()
    settings = schema["$defs"]["RuleSettings"]

    assert set(settings["properties"]) == (
        {rule.name for rule in build_rule_set().rules}
        | {family.value for family in Category}
        | {"ALL"}
    )
    assert settings["additionalProperties"] is False
    for rule in build_rule_set().rules:
        entry = settings["properties"][rule.name]
        assert entry["$ref"] == "#/$defs/RuleSetting"
        assert rule.code in entry["title"]
        assert rule.explain().bad in entry["markdownDescription"]
        assert rule.explain().good in entry["markdownDescription"]
        assert settings["patternProperties"][f"^{rule.code}$"] == entry
    assert schema["$defs"]["StyleOverride"]["properties"]["rules"] == {
        "$ref": "#/$defs/SourceRuleSettings"
    }
    assert schema["$defs"]["RuleSeverity"]["enum"] == [
        "error",
        "warning",
        "hint",
        "on",
        "off",
    ]
    assert schema["$defs"]["RuleOverride"]["required"] == ["severity", "reason"]


def test_editor_schema_accepts_inline_style_reasons() -> None:
    schema = config_json_schema()
    definitions = schema["$defs"]

    assert {"$ref": "#/$defs/MemberStyleConfig"} in schema["properties"]["style"][
        "anyOf"
    ]
    for name in ("StyleConfig", "MemberStyleConfig", "StyleOverride"):
        properties = definitions[name]["properties"]
        assert properties["reasons"]["deprecated"] is True
        _, reasoned = properties["max_positional_parameters"]["anyOf"]
        assert reasoned["required"] == ["value", "reason"]
        assert reasoned["additionalProperties"] is False


def test_editor_schema_does_not_advertise_removed_switches() -> None:
    schema = config_json_schema()

    assert "preset" not in schema["properties"]
    tests = schema["$defs"]["TestsConfig"]["properties"]
    assert not any(
        key.startswith("review_") or key.startswith("require_") for key in tests
    )
    assert "enabled" not in schema["$defs"]["OrderingConfig"]["properties"]
    contract = schema["$defs"]["StyleOverride"]["properties"]
    assert (
        not {"member_visibility", "static_methods", "instance_assignments"}
        & contract.keys()
    )


def test_package_completion_and_runtime_rule_scope_agree() -> None:
    schema = config_json_schema()
    source = schema["$defs"]["SourceRuleSettings"]["properties"]

    source_rules = [
        rule
        for rule in build_rule_set().rules
        if getattr(rule, "scope", "source") == "source"
    ]
    assert set(source) == (
        {rule.name for rule in source_rules}
        | {rule.category.value for rule in source_rules}
        | {"ALL"}
    )
    assert "tests" not in source
    assert "tests/unbounded-wait" not in source["ALL"]["description"]
    assert "architecture/uncovered-source" not in source["architecture"]["description"]
    assert "visibility/test-only-public-attribute" in source
    assert "tests/unbounded-wait" not in source
    assert "architecture/uncovered-source" not in source
