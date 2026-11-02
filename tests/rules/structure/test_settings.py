from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT327",))


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/settings.py": source},
    )


@pytest.mark.parametrize(
    "annotation",
    [
        "str",
        "bytes | None",
        '"str | None"',
        "Optional[str]",
        'Annotated[str, "credential"]',
        "SecretStr | str",
    ],
)
def test_secret_settings_fields_accepting_plaintext_are_reviewed(
    tmp_path: Path, annotation: str
) -> None:
    source = f"from pydantic_settings import BaseSettings as SettingsBase\nfrom pydantic import SecretStr\nfrom typing import Optional, Annotated\nclass Settings(SettingsBase):\n    api_key: {annotation}\n"
    root = _project(tmp_path, source)
    [finding] = violations(root, OPTIONS)
    assert (finding.code, finding.line) == ("SMT327", 5)
    assert finding.expected is not None
    assert finding.expected["field"] == "api_key"
    assert (root / "app/settings.py").read_text() == source


def test_same_file_subclasses_and_module_aliases_are_supported(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "import pydantic_settings as ps\nclass Common(ps.BaseSettings): pass\nclass Settings(Common):\n    database_password: str\n",
    )
    assert [(v.code, v.line) for v in violations(root, OPTIONS)] == [("SMT327", 4)]


def test_secret_types_nonsecret_fields_unknown_types_and_other_models_are_preserved(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "from pydantic_settings import BaseSettings\nfrom pydantic import BaseModel, SecretStr, SecretBytes, Secret\nclass Settings(BaseSettings):\n    api_key: SecretStr\n    database_password: SecretStr | None = None\n    client_secret: SecretBytes\n    access_token: Secret[str]\n    public_key: str\n    token_count: int\n    api_key_name: str\n    private_key: CustomCredential\nclass User(BaseModel):\n    password: str\n",
    )
    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize(
    "shadow",
    ["BaseSettings = custom", "str = custom", "def helper(BaseSettings): pass"],
)
def test_shadowed_bindings_are_not_guessed(tmp_path: Path, shadow: str) -> None:
    root = _project(
        tmp_path,
        f"from pydantic_settings import BaseSettings\n{shadow}\nclass Settings(BaseSettings):\n    password: str\n",
    )
    assert violations(root, OPTIONS) == []


def test_default_values_are_never_exposed_in_findings(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        'from pydantic_settings import BaseSettings\nclass Settings(BaseSettings):\n    password: str = "synthetic-credential-must-not-appear"\n',
    )
    [finding] = violations(root, OPTIONS)
    assert "synthetic-credential-must-not-appear" not in str(finding)


def test_opt_in_package_override_and_suppression(tmp_path: Path) -> None:
    source = "from pydantic_settings import BaseSettings\nclass Settings(BaseSettings):\n    password: str\n"
    root = _project(tmp_path, source)
    assert violations(root) == []
    _project(tmp_path, source, CONFIG + "rules: {secret-settings-field: warning}\n")
    assert [v.code for v in violations(root)] == ["SMT327"]
    _project(
        tmp_path,
        source,
        CONFIG
        + "structure:\n  contracts:\n    legacy: {match: app, rules: {secret-settings-field: off}}\n",
    )
    assert violations(root, OPTIONS) == []
    _project(
        tmp_path,
        source.replace(
            "password: str",
            "password: str  # smelt: ignore[SMT327] -- Nonsecret protocol vocabulary.",
        ),
    )
    assert violations(root, OPTIONS) == []
