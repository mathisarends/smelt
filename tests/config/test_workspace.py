from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError, load_config
from smelt.config.workspace import member_directories
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path

ROOT = (
    "version: 1\nproject: {workspace: uv, source_roots: [src], test_roots: [tests]}\n"
)
PYPROJECT = '[tool.uv.workspace]\nmembers = ["services/*", "packages/*"]\nexclude = ["packages/legacy"]\n'


def _workspace(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    return write_project(
        tmp_path,
        {
            "pyproject.toml": PYPROJECT,
            "smelt.yml": ROOT,
            "services/api/src/api/__init__.py": "",
            "services/api/tests/test_api.py": "",
            "packages/core/src/core/__init__.py": "",
            "packages/legacy/src/legacy/__init__.py": "",
            "tests/test_e2e.py": "",
            **(files or {}),
        },
    )


def test_roots_apply_inside_every_declared_member(tmp_path: Path) -> None:
    project = load_config(_workspace(tmp_path) / "smelt.yml").config.project

    assert project.source_roots == ["packages/core/src", "services/api/src"]
    assert project.test_roots == ["tests", "services/api/tests"]
    assert project.root_packages == ["core", "api"]


@pytest.mark.parametrize("pattern", ["", "../outside", "/absolute", "C:\\absolute"])
def test_member_boundary_globs_stay_inside_the_workspace(
    tmp_path: Path, pattern: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT
            + f"architecture:\n  members:\n    '{pattern}': {{may_depend_on: []}}\n"
        },
    )

    with pytest.raises(ConfigError, match="workspace-relative"):
        load_config(root / "smelt.yml")


def test_member_boundaries_require_a_uv_workspace(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\narchitecture: {members: {'packages/*': {may_depend_on: []}}}\n",
            "app/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError, match=r"requires project\.workspace: uv"):
        load_config(root / "smelt.yml")


def test_a_root_project_contributes_its_packages_but_not_member_folders(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "gateway"\n' + PYPROJECT,
            "smelt.yml": "version: 1\nproject: {workspace: uv}\n",
            "gateway/__init__.py": "",
        },
    )

    project = load_config(root / "smelt.yml").config.project

    assert project.root_packages == ["gateway", "core", "api"]


def test_member_directories_report_why_a_member_is_skipped(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": PYPROJECT.replace(
                '"packages/*"', '"packages/*", "tools/*"'
            )
        },
    )

    assert [(m.path, m.skipped) for m in member_directories(root)] == [
        ("packages/core", None),
        ("packages/legacy", "excluded by tool.uv.workspace.exclude"),
        ("services/api", None),
        ("tools/*", "matches no directory"),
    ]


def test_member_structure_becomes_a_contract_for_each_member_package(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "services/api/src/admin/__init__.py": "",
            "services/api/smelt.yml": "style: {max_positional_parameters: 2}\narchitecture:\n  packages:\n    http: {match: api.http, public: ['.']}\n",
            "packages/core/smelt.yml": "style: {future_annotations: forbid}\n",
        },
    )

    contracts = load_config(root / "smelt.yml").config.structure.contracts

    assert {name: c.match for name, c in contracts.items()} == {
        "packages/core": "core",
        "services/api:admin": "admin",
        "services/api:api": "api",
        "services/api:http": "api.http",
    }
    assert contracts["services/api:api"].max_positional_parameters == 2
    assert contracts["packages/core"].future_annotations == "forbid"


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("max_positional_parameters", "10"),
        ("max_positional_parameters", "0"),
        ("dataclass_options", "[]"),
        ("future_annotations", "allow"),
    ],
)
def test_member_style_changes_to_root_values_require_reasons(
    tmp_path: Path, option: str, value: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT + "extends: base.yml\n",
            "base.yml": """
                style:
                  max_positional_parameters: 3
                  dataclass_options: [slots]
                  future_annotations: when-needed
                """,
            "packages/core/smelt.yml": f"style: {{{option}: {value}}}\n",
        },
    )

    with pytest.raises(ConfigError, match="requires a reason") as caught:
        load_config(root / "smelt.yml")

    [issue] = caught.value.issues
    assert issue.path == f"style.{option}"
    assert issue.source
    assert issue.source.endswith("packages/core/smelt.yml")


def test_member_style_reasons_survive_compilation(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT
            + "style: {max_positional_parameters: 3, dataclass_options: [slots]}\n",
            "packages/core/smelt.yml": """
                style:
                  max_positional_parameters: 0
                  dataclass_options: []
                  reasons:
                    max_positional_parameters: " Prefer named arguments. "
                    dataclass_options: Generated classes predate slots.
                """,
            "services/api/smelt.yml": """
                style:
                  max_positional_parameters: 3
                  future_annotations: when-needed
                """,
        },
    )

    config = load_config(root / "smelt.yml").config
    contract = config.structure.contracts["packages/core"]
    data = config.model_dump(mode="json", by_alias=True)

    assert contract.max_positional_parameters == 0
    assert contract.dataclass_options == []
    assert data["members"]["packages/core"]["style"]["reasons"] == {
        "max_positional_parameters": "Prefer named arguments.",
        "dataclass_options": "Generated classes predate slots.",
    }
    assert (
        config.structure.contracts["services/api"].future_annotations == "when-needed"
    )


@pytest.mark.parametrize("reason", ["''", "'   '"])
def test_member_style_rejects_blank_reasons(tmp_path: Path, reason: str) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT + "style: {max_positional_parameters: 3}\n",
            "packages/core/smelt.yml": (
                "style:\n  max_positional_parameters: 10\n"
                f"  reasons: {{max_positional_parameters: {reason}}}\n"
            ),
        },
    )

    with pytest.raises(ConfigError) as caught:
        load_config(root / "smelt.yml")

    assert caught.value.issues[0].path == "style.reasons.max_positional_parameters"


def test_member_contract_style_cannot_override_root_contract_without_a_reason(
    tmp_path: Path,
) -> None:
    member = "services/api/smelt.yml"
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT
            + "style:\n  overrides:\n    domain: {match: api.*, future_annotations: forbid}\n",
            member: """
                style:
                  overrides:
                    legacy:
                      match: api.legacy
                      future_annotations: allow
                """,
        },
    )

    with pytest.raises(ConfigError, match="requires a reason") as caught:
        load_config(root / "smelt.yml")

    assert caught.value.issues[0].path == "style.overrides.legacy.future_annotations"
    write_project(
        root,
        {
            member: """
                style:
                  overrides:
                    legacy:
                      match: api.legacy
                      future_annotations: allow
                      reasons: {future_annotations: Legacy modules use forward references.}
                """,
        },
    )
    data = load_config(root / "smelt.yml").config.model_dump(mode="json")
    assert data["members"]["services/api"]["style"]["overrides"]["legacy"][
        "reasons"
    ] == {"future_annotations": "Legacy modules use forward references."}


def test_member_paths_are_relative_to_the_member(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "services/api/smelt.yml": """
                project:
                  exclude: [{path: src/api/generated/**, reason: Generated client.}]
                ignore:
                  - {rule: complexity, paths: [tests/**], reason: Table-driven fixtures.}
                """,
        },
    )

    config = load_config(root / "smelt.yml").config

    assert config.project.exclude_patterns == ["services/api/src/api/generated/**"]
    assert config.ignore[0].paths == ["services/api/tests/**"]


@pytest.mark.parametrize(
    ("member", "location", "message"),
    [
        ("extends: ../../base.yml\n", "extends", "inherits the workspace root"),
        (
            "architecture: {shared: [core.shared]}\n",
            "architecture.shared[0]",
            "not inside this member's packages",
        ),
        (
            "architecture: {import_graph: {transitive: true}}\n",
            "architecture.import_graph",
            "set once in the workspace root",
        ),
        (
            "architecture: {members: {}}\n",
            "architecture.members",
            "set once in the workspace root",
        ),
        ("project: {source_roots: [lib]}\n", "project.source_roots", "only add"),
        ("debt: ../debt.json\n", "debt", "member-relative"),
        ("rules: {Nope: error}\n", "rules", "not a rule code"),
        (
            "architecture:\n  packages:\n    x: {match: core, public: ['.']}\n",
            "architecture.packages.x.match",
            "outside this member",
        ),
    ],
)
def test_member_errors_name_the_member_file_and_its_own_key(
    tmp_path: Path, member: str, location: str, message: str
) -> None:
    root = _workspace(tmp_path, {"services/api/smelt.yml": member})

    with pytest.raises(ConfigError, match=message) as caught:
        load_config(root / "smelt.yml")

    issue = caught.value.issues[0]
    assert issue.path == location
    assert (issue.source or caught.value.source or "").endswith(
        "services/api/smelt.yml"
    )


def test_package_names_must_be_unique_across_members(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {"packages/core/src/api/__init__.py": ""})

    with pytest.raises(ConfigError, match='package "api" exists in workspace members'):
        load_config(root / "smelt.yml")


def test_empty_member_architecture_inherits_root(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT + "architecture: {shared: [core]}\n",
            "packages/core/smelt.yml": "architecture: {}\n",
        },
    )

    config = load_config(root / "smelt.yml").config

    assert config.members["packages/core"].architecture is None
    assert config.architecture.shared == ["core"]


def test_member_package_api_can_inherit_root_layers(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT + "architecture: {layers: {domain: {path: domain}}}\n",
            "packages/core/smelt.yml": """
                architecture:
                  packages:
                    public-api: {match: core, public: ['.']}
                style:
                  overrides:
                    public-api: {match: core, max_positional_parameters: 2}
                """,
        },
    )

    loaded = load_config(root / "smelt.yml").config

    assert not loaded.members["packages/core"].governs_architecture
    contract = loaded.structure.contracts["packages/core:public-api"]
    assert contract.public == ["."]
    assert contract.max_positional_parameters == 2
    assert set(loaded.architecture.layers) == {"domain"}


def test_root_architecture_error_names_the_governing_member(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": ROOT + "architecture: {shared: [core]}\n",
            "packages/core/smelt.yaml": "architecture: {shared: [core.ids]}\n",
        },
    )

    with pytest.raises(
        ConfigError, match=r"member architecture.*packages/core"
    ) as caught:
        load_config(root / "smelt.yml")

    assert caught.value.issues[0].path == "architecture.shared[0]"


def test_loading_a_member_config_selects_the_workspace_root(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {"services/api/smelt.yml": "rules: {complexity/duplicate-function: on}\n"},
    )

    loaded = load_config(root / "services/api/smelt.yml")

    assert loaded.path == (root / "smelt.yml").resolve()
    assert loaded.member == "services/api"
    assert loaded.config.members["services/api"].rules == {
        "complexity/duplicate-function": "on"
    }
    assert loaded.inputs[-1] == (root / "services/api/smelt.yml").resolve()


def test_without_a_workspace_root_a_nested_config_stays_its_own_project(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [api]}\n",
            "services/api/smelt.yml": "version: 1\nproject: {source_roots: [src]}\n",
        },
    )

    loaded = load_config(root / "services/api/smelt.yml")

    assert loaded.member is None
    assert loaded.root == (root / "services/api").resolve()
    assert loaded.config.members == {}


def test_the_root_cannot_declare_members_inline(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path, {"smelt.yml": ROOT + "members: {services/api: {rules: {ALL: on}}}\n"}
    )

    with pytest.raises(ConfigError, match="each workspace member keeps its own"):
        load_config(root / "smelt.yml")
