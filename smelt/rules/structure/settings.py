from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.settings import secret_settings
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class SecretSettingsField(Rule):
    file_local = True
    code = "SMT327"
    name = "secret-settings-field"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A secret-named settings field accepts an unwrapped str or bytes value.",
        rationale="Pydantic SecretStr and SecretBytes mask ordinary string/repr/JSON representations. This opt-in review recognizes top-level, undecorated BaseSettings classes and same-file subclasses through stable direct import aliases. Secret names are password, passwd, secret, api_key, private_key, access_token, refresh_token and token, including underscore-delimited suffixes. It reviews str/bytes inside quoted annotations, unions, Optional and Annotated. Correct Secret types, unknown/custom types, cross-file inheritance, dynamic aliases and shadowed bindings are outside this check. Public keys, token counts and nonsecret settings remain valid. Field names are review evidence, not proof of sensitivity or absence of leaks. Defaults and secret values are never included in diagnostics.",
        bad="from pydantic_settings import BaseSettings\n\nclass Settings(BaseSettings):\n    api_key: str\n    database_password: str | None = None",
        good="from pydantic import SecretStr\nfrom pydantic_settings import BaseSettings\n\nclass Settings(BaseSettings):\n    api_key: SecretStr\n    database_password: SecretStr | None = None",
        fix="Use SecretStr for textual credentials or SecretBytes for binary credentials. Update deliberate plaintext consumers to unwrap explicitly and verify serialization. Keep nonsecret fields unchanged or suppress a misleading field name with a reason. No type annotations or consumers are rewritten automatically. Enable rules: {secret-settings-field: warning}; package overrides can restrict adoption.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            for setting in secret_settings(ctx.asts.parse(source.path)):
                yield self.violation(
                    f"review secret settings field {setting.owner}.{setting.name}; use a masked secret type",
                    path=source.path,
                    line=setting.node.lineno,
                    column=setting.node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": setting.owner,
                        "field": setting.name,
                        "plain_types": list(setting.plain_types),
                        "suggested_types": [
                            "SecretBytes" if kind == "bytes" else "SecretStr"
                            for kind in setting.plain_types
                        ],
                        "automatic_rewrite": False,
                    },
                    hint="Use SecretStr/SecretBytes for credentials; review plaintext consumers and serialization explicitly.",
                )
