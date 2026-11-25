from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class SecretSettingsField(Rule):
    code = "FWK001"
    name = "frameworks/prefer-secret-type"
    category = Category.FRAMEWORKS
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A secret-named settings field accepts an unwrapped str or bytes value.",
        rationale="Pydantic SecretStr and SecretBytes mask ordinary string/repr/JSON representations. This opt-in review recognizes top-level, undecorated BaseSettings classes and same-file subclasses through stable direct import aliases. Secret names are password, passwd, secret, api_key, private_key, access_token, refresh_token and token, including underscore-delimited suffixes. It reviews str/bytes inside quoted annotations, unions, Optional and Annotated. Correct Secret types, unknown/custom types, cross-file inheritance, dynamic aliases and shadowed bindings are outside this check. Public keys, token counts and nonsecret settings remain valid. Field names are review evidence, not proof of sensitivity or absence of leaks. Defaults and secret values are never included in diagnostics.",
        bad="from pydantic_settings import BaseSettings\n\nclass Settings(BaseSettings):\n    api_key: str\n    database_password: str | None = None",
        good="from pydantic import SecretStr\nfrom pydantic_settings import BaseSettings\n\nclass Settings(BaseSettings):\n    api_key: SecretStr\n    database_password: SecretStr | None = None",
        fix="Use SecretStr for textual credentials or SecretBytes for binary credentials. Update deliberate plaintext consumers to unwrap explicitly and verify serialization. Keep nonsecret fields unchanged or suppress a misleading field name with a reason. No type annotations or consumers are rewritten automatically. Enable rules: {frameworks/prefer-secret-type: warning}; package overrides can restrict adoption.",
        canonical="Type secret settings fields as SecretStr or SecretBytes",
        config=("rules", "style.overrides"),
    )
