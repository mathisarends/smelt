from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.errors import AnalysisError
from smelt.engine.check import CheckOptions
from tests.helpers import MEMBER_BOUNDARIES, violations, write_policy_project

if TYPE_CHECKING:
    from pathlib import Path


def _workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    return write_policy_project(tmp_path, {**MEMBER_BOUNDARIES, **files})


def test_allowed_member_and_same_member_imports_pass(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "packages/core/src/core/consumer.py": "from core.model import Order\nimport requests\n"
        },
    )

    assert violations(root) == []


def test_member_boundary_reports_the_import_and_semantic_edge(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {"packages/core/src/core/model.py": "from api.http import Order\n"},
    )

    [found] = violations(root, CheckOptions(select=("ARC009",)))

    assert (found.code, found.path, found.line, found.column, found.member) == (
        "ARC009",
        "packages/core/src/core/model.py",
        1,
        22,
        "packages/core",
    )
    assert found.target_module == "api.http"
    assert found.edge == "packages/core -> services/api"
    assert found.expected == {
        "source_member": "packages/core",
        "target_member": "services/api",
        "may_depend_on": [],
    }


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("members: {'packages/*': {may_depend_on: []}}", []),
        ("members: {'services/*': {may_depend_on: []}}", ["ARC009"]),
        (
            "members: {'services/*': {may_depend_on: []}, 'services/api': {may_depend_on: ['packages/*']}}",
            [],
        ),
    ],
)
def test_unmatched_members_and_overlapping_globs(
    tmp_path: Path, policy: str, expected: list[str]
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {workspace: uv, source_roots: [src]}\narchitecture:\n  "
            + policy
            + "\n"
        },
    )

    assert [v.code for v in violations(root)] == expected


@pytest.mark.parametrize("mode", ["include", "ignore"])
def test_type_checking_imports_follow_root_policy(tmp_path: Path, mode: str) -> None:
    root = _workspace(
        tmp_path,
        {
            "packages/core/src/core/model.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from api.http import Order\n",
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + f"  import_graph: {{type_checking: {mode}}}\n",
        },
    )

    assert [v.code for v in violations(root)] == (
        ["ARC009", "ARC010"] if mode == "include" else []
    )


def test_missing_declaration_is_separate_from_allowed_boundary(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "services/api/pyproject.toml": '[project]\nname = "web-api"\ndependencies = []\n[tool.uv.sources]\ncore-lib = {workspace = true}\n'
        },
    )

    [found] = violations(root)

    assert found.code == "ARC010"
    assert found.member == "services/api"
    assert found.hint
    assert "Declare core-lib" in found.hint
    assert found.expected == {
        "source_member": "services/api",
        "target_member": "packages/core",
        "dependency": "core-lib",
        "declared_members": [],
    }


@pytest.mark.parametrize("selection", [(), ("ARC010",)])
def test_forbidden_dependency_repair_does_not_recommend_declaring_it(
    tmp_path: Path, selection: tuple[str, ...]
) -> None:
    root = _workspace(
        tmp_path,
        {"packages/core/src/core/model.py": "from api.http import Order\n"},
    )

    found = violations(root, CheckOptions(select=selection))

    assert [v.code for v in found] == (
        ["ARC010"] if selection else ["ARC009", "ARC010"]
    )
    hint = found[-1].hint
    assert hint
    assert "Remove this import" in hint
    assert "Declare" not in hint


@pytest.mark.parametrize(
    "metadata",
    [
        '[project]\nname = "web-api"\ndependencies = ["Core_Lib[fast]>=1; python_version < \'3.0\'"]\n',
        '[project]\nname = "web-api"\ndependencies = ["core-lib"]\n[tool.uv.sources]\ncore-lib = {path = "../../packages/core"}\n',
        '[project]\nname = "web-api"\n[project.optional-dependencies]\norders = ["core-lib"]\n',
        '[project]\nname = "web-api"\ndynamic = ["dependencies"]\n',
        "",
    ],
)
def test_declarations_use_distribution_names_sources_and_inference_limits(
    tmp_path: Path, metadata: str
) -> None:
    root = _workspace(tmp_path, {"services/api/pyproject.toml": metadata})

    assert violations(root) == []


def test_root_uv_source_paths_are_root_relative(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": MEMBER_BOUNDARIES["pyproject.toml"]
            + '[tool.uv.sources]\ncore-lib = {path = "packages/core"}\n',
            "services/api/pyproject.toml": '[project]\nname = "web-api"\ndependencies = ["core-lib"]\n',
        },
    )

    assert violations(root) == []


def test_member_source_replaces_the_root_source_even_with_a_marker(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": MEMBER_BOUNDARIES["pyproject.toml"]
            + "[tool.uv.sources]\ncore-lib = {workspace = true}\n",
            "services/api/pyproject.toml": '[project]\nname = "web-api"\ndependencies = ["core-lib"]\n[tool.uv.sources]\ncore-lib = [{path = "../../vendor/core", marker = "sys_platform == \'other\'"}]\n',
        },
    )

    [found] = violations(root)

    assert found.code == "ARC010"
    assert found.member == "services/api"


def test_undeclared_import_of_a_namespace_package_is_reported(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "services/api/pyproject.toml": '[project]\nname = "web-api"\n',
            "services/api/src/api/http.py": "import core\n",
        },
    )
    (root / "packages/core/src/core/__init__.py").unlink()

    [found] = violations(root)

    assert found.code == "ARC010"
    assert found.target_module == "core"


def test_indirect_imports_do_not_require_direct_dependency_declarations(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "  import_graph: {transitive: true}\n",
            "packages/core/pyproject.toml": '[project]\nname = "core-lib"\ndependencies = ["storage"]\n',
            "packages/core/src/core/model.py": "from storage.db import Order\n",
            "packages/storage/pyproject.toml": '[project]\nname = "storage"\n',
            "packages/storage/src/storage/__init__.py": "",
            "packages/storage/src/storage/db.py": "class Order: pass\n",
        },
    )

    assert violations(root, CheckOptions(select=("ARC010",))) == []


@pytest.mark.parametrize(
    "metadata", ["[project", '[project]\ndependencies = ["!bad"]\n']
)
def test_unreadable_metadata_is_an_analysis_error(
    tmp_path: Path, metadata: str
) -> None:
    root = _workspace(tmp_path, {"services/api/pyproject.toml": metadata})

    with pytest.raises(AnalysisError, match=r"services/api/pyproject\.toml"):
        violations(root)


def test_member_rules_and_suppressions_retain_their_scope(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "packages/core/smelt.yml": "rules: {architecture/member-boundary: {severity: warning, reason: Reviewing the migration boundary.}}\n",
            "packages/core/src/core/model.py": "from api.http import Order  # smelt: ignore[ARC010] -- Host provides the API during migration.\n",
        },
    )

    [found] = violations(root)

    assert found.code == "ARC009"
    assert found.severity.value == "warning"


def test_root_project_participates_as_dot(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "gateway"\n'
            + MEMBER_BOUNDARIES["pyproject.toml"],
            "src/gateway/__init__.py": "from core.model import Order\n",
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "    '.': {may_depend_on: []}\n",
        },
    )

    found = violations(root)

    assert [v.code for v in found] == ["ARC009", "ARC010"]
    assert all(v.member == "." for v in found)


@pytest.mark.parametrize(
    "api_config",
    [
        "",
        "architecture: {packages: {api: {match: api, public: ['.']}}}",
        "architecture: {layers: {infra: {path: infra}}}",
    ],
)
def test_local_layer_checks_do_not_cross_member_boundaries(
    tmp_path: Path, api_config: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "  layers: {domain: {path: domain}, infra: {path: infra}}\n",
            "pyproject.toml": '[project]\nname = "root-app"\n'
            + MEMBER_BOUNDARIES["pyproject.toml"],
            "src/root_app/__init__.py": "",
            "src/root_app/domain/__init__.py": "",
            "src/root_app/domain/service.py": "import root_app.infra.storage\nimport api.infra.http\n",
            "src/root_app/infra/__init__.py": "",
            "src/root_app/infra/storage.py": "",
            "services/api/src/api/infra/__init__.py": "",
            "services/api/src/api/infra/http.py": "",
            "services/api/smelt.yml": api_config,
        },
    )

    [finding] = violations(root, CheckOptions(select=("ARC001",)))

    assert finding.target_module == "root_app.infra.storage"


@pytest.mark.parametrize("source_roots", ["", "  source_roots: [src]\n"])
def test_explicit_namespace_package_uses_its_member_layers(
    tmp_path: Path, source_roots: str
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject:\n  workspace: uv\n  root_packages: [core, plugins]\n"
            + source_roots,
            "services/api/smelt.yml": "architecture: {layers: {domain: {path: domain}, infra: {path: infra}}}\n",
            "services/api/src/plugins/domain/handlers/service.py": "import plugins.infra.adapters.storage\n",
            "services/api/src/plugins/infra/adapters/storage.py": "",
        },
    )

    [finding] = violations(root, CheckOptions(select=("ARC001",)))

    assert finding.member == "services/api"
    assert finding.target_module == "plugins.infra.adapters.storage"


def test_transitive_third_party_check_uses_only_paths_inside_the_owner(
    tmp_path: Path,
) -> None:
    root = _workspace(
        tmp_path,
        {
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "  import_graph: {transitive: true}\n",
            "packages/core/src/core/model.py": "import requests\n",
            "services/api/smelt.yml": """
                architecture:
                  layers:
                    domain: {path: domain, third_party: {default: deny}}
                    infra: {path: infra}
                """,
            "services/api/src/api/domain/remote.py": "import core.model\n",
            "services/api/src/api/domain/local.py": "import core.model\nimport api.infra.bridge\n",
            "services/api/src/api/infra/bridge.py": "import api.infra.client\n",
            "services/api/src/api/infra/client.py": "import requests\n",
        },
    )

    [finding] = violations(root, CheckOptions(select=("ARC003",)))

    assert finding.source_module == "api.domain.local"
    assert [link.imported for link in finding.import_chain] == [
        "api.infra.bridge",
        "api.infra.client",
        "requests",
    ]
