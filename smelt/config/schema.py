from typing import Any

from smelt.config.models import SmeltConfig

SCHEMA_ID = (
    "https://raw.githubusercontent.com/mathisarends/smelt/main/smelt.schema.json"
)


def config_json_schema() -> dict[str, Any]:
    schema = SmeltConfig.model_json_schema()
    # Bases are policy fragments; required fields are checked after composition.
    schema.pop("required", None)
    for definition in schema.get("$defs", {}).values():
        definition.pop("required", None)
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
