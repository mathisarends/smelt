from typing import Any

from smelt.config.models import PackagePolicy, SmeltConfig

SCHEMA_ID = (
    "https://raw.githubusercontent.com/mathisarends/smelt/main/smelt.schema.json"
)


def config_json_schema() -> dict[str, Any]:
    schema = SmeltConfig.model_json_schema()
    # Bases are policy fragments; required fields are checked after composition.
    schema.pop("required", None)
    for definition in schema.get("$defs", {}).values():
        definition.pop("required", None)
    # An entry sets max, or explains its severity with a reason.
    schema["$defs"]["RuleOverride"]["anyOf"] = [
        {"required": ["max"]},
        {"required": ["severity", "reason"]},
    ]
    _describe_style_values(schema["$defs"])
    # Member configs are separate files; the root never writes them inline.
    schema["properties"].pop("members", None)
    for name in ("MemberConfig", "MemberProjectConfig"):
        schema.get("$defs", {}).pop(name, None)
    # Root and member files share the editor schema.
    schema["properties"]["style"] = {
        "anyOf": [
            schema["properties"]["style"],
            {"$ref": "#/$defs/MemberStyleConfig"},
        ],
    }
    schema["properties"]["extends"] = {
        "type": "string",
        "minLength": 1,
        "description": "One local base file, relative to this file. Mappings merge; lists replace. All project paths remain relative to the selected workspace config.",
    }
    # Allow the short forms `third_party: allow | deny`.
    policy = schema.get("$defs", {}).get("ThirdPartyPolicy")
    if policy is not None:
        schema["$defs"]["ThirdPartyPolicy"] = {
            "anyOf": [{"enum": ["allow", "deny"], "type": "string"}, policy],
        }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        **schema,
        "title": "smelt.yml",
    }


def _describe_style_values(definitions: dict[str, Any]) -> None:
    for model in ("StyleConfig", "StyleOverride", "MemberStyleConfig"):
        properties = definitions[model]["properties"]
        properties["reasons"]["deprecated"] = True
        for name in PackagePolicy.model_fields:
            option = properties[name]
            properties[name] = {
                "description": option.get("description", ""),
                "anyOf": [
                    option,
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["value", "reason"],
                        "properties": {
                            "value": {"allOf": [option, {"not": {"type": "null"}}]},
                            "reason": {
                                "type": "string",
                                "minLength": 1,
                                "pattern": r"\S",
                            },
                        },
                    },
                ],
            }
