from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from smelt.analysis.imports import ImportIndex
from smelt.cli import main
from smelt.config import load_config
from smelt.diagnostics.debt import Debt, DebtEntry, fingerprint
from tests.helpers import (
    FIXTURES,
    LAYERED_CONFIG,
    MEMBER_BOUNDARIES,
    add_rules,
    check,
    write_policy_project,
)

GATEWAY = FIXTURES / "gateway"


@pytest.mark.parametrize("format_name", ["text", "concise"])
def test_statistics_by_member_includes_source_findings_and_test_hints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    format_name: str,
) -> None:
    write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "rules: {tests/independent-case-loop: hint}\n",
            "packages/core/src/core/model.py": "from api.http import Order\n",
            "services/api/tests/test_http.py": "def test_http():\n    for case in [1, 2]:\n        selected = select(case)\n        assert selected == case\n",
        },
    )
    monkeypatch.chdir(tmp_path)

    code, out, err = _run(
        capsys, "check", "--statistics", "--by", "member", "--format", format_name
    )

    assert code == 1
    assert err == ""
    assert "2  packages/core" in out
    assert "1  services/api" in out
    _, out, _ = _run(capsys, "check", "--format", "json")
    found = json.loads(out)["violations"]
    assert [(v["code"], v["member"]) for v in found] == [
        ("ARC009", "packages/core"),
        ("ARC010", "packages/core"),
        ("TST009", "services/api"),
    ]


def test_upgrade_guide_is_printed_once_and_retained_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "smelt.yml"
    policy.write_text(
        "version: 2\nstructure: {}\nrules: {import-cycle: error}\n", encoding="utf-8"
    )
    code, _, err = _run(capsys, "--config", str(policy), "check")
    assert code == 2
    assert err.count("docs/upgrading.md") == 1
    code, out, _ = _run(capsys, "--config", str(policy), "check", "--format", "json")
    assert code == 2
    assert out.count("docs/upgrading.md") == 2


def test_statistics_grouping_requires_statistics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code, out, _ = _run(capsys, "check", "--by", "member", "--format", "json")

    assert code == 2
    assert "requires --statistics" in json.loads(out)["error"]["message"]


def test_inherited_config_show_explains_sources_and_is_cwd_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy_project(
        tmp_path,
        {
            "policy/base.yml": "version: 2\nproject: {source_roots: [src], test_roots: [tests], exclude: [src/app/generated/**]}\ntests: {quality_exclude: [tests/generated/**]}\ndebt: .smelt/debt.json\nrules: {architecture/import-cycle: error, tests/unbounded-wait: error}\n",
            "runtime/smelt.yml": "extends: ../policy/base.yml\nrules: {architecture/import-cycle: warning}\n",
            "runtime/src/app/__init__.py": "",
            "runtime/tests/test_wait.py": "import subprocess\ndef test_wait():\n    subprocess.run(['tool'], check=True)\n",
            "runtime/tests/generated/test_generated.py": "import subprocess\ndef test_generated():\n    subprocess.run(['tool'], check=True)\n",
        },
    )
    monkeypatch.chdir(tmp_path)
    code, output, error = _run(
        capsys, "config", "show", "--config", "runtime/smelt.yml", "--format", "json"
    )
    assert code == 0
    assert error == ""
    config = json.loads(output)
    assert config["project"]["root_packages"] == ["app"]
    assert config["project"]["source_roots"] == ["src"]
    assert config["debt"] == ".smelt/debt.json"
    assert "max_positional_parameters" not in config["style"]
    assert config["rules"] == {
        "architecture/import-cycle": "warning",
        "tests/unbounded-wait": "error",
    }
    before = _run(capsys, "check", "--config", "runtime/smelt.yml", "--format", "json")
    assert before[0] == 1
    assert [v["path"] for v in json.loads(before[1])["violations"]] == [
        "tests/test_wait.py"
    ]

    monkeypatch.chdir(tmp_path / "runtime")
    assert _run(capsys, "config", "show", "--format", "json") == (code, output, error)
    assert _run(capsys, "check", "--format", "json") == before
    code, output, error = _run(
        capsys, "config", "show", "--sources", "--format", "json"
    )
    explained = json.loads(output)
    assert code == 0
    assert error == ""
    assert explained["config"] == config
    assert explained["sources"] == [
        (tmp_path / "policy/base.yml").resolve().as_posix(),
        (tmp_path / "runtime/smelt.yml").resolve().as_posix(),
    ]
    assert explained["origins"]["rules.architecture/import-cycle"].endswith(
        "runtime/smelt.yml"
    )
    assert explained["origins"]["debt"].endswith("policy/base.yml")


def test_inherited_policy_errors_keep_file_origins_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy_project(
        tmp_path,
        {
            "base.yml": "rules: {unknown-rule: error}\n",
            "smelt.yml": "extends: base.yml\nversion: 2\nproject: {root_packages: [app]}\n",
        },
    )
    code, output, _ = _run(
        capsys,
        "config",
        "show",
        "--config",
        str(tmp_path / "smelt.yml"),
        "--format",
        "json",
    )
    assert code == 2
    [issue] = json.loads(output)["error"]["config"]["issues"]
    assert issue["location"] == "rules.unknown-rule"
    assert issue["file"].endswith("base.yml")


def test_a_workspace_rule_replaces_its_base_under_another_alias(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy_project(
        tmp_path,
        {
            "base.yml": "rules: {architecture/import-cycle: error}\n",
            "smelt.yml": "extends: base.yml\nversion: 2\nproject: {root_packages: [app]}\nrules: {ARC004: off}\n",
        },
    )
    code, _, error = _run(
        capsys, "config", "show", "--config", str(tmp_path / "smelt.yml")
    )
    assert code == 0
    assert error == ""


def test_context_attributes_rules_to_the_layer_that_supplied_the_severity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "rules.yml": "rules: {complexity/excess-positional-parameters: warning}\n",
            "base.yml": """
                extends: rules.yml
                rules:
                  architecture/import-cycle: {severity: error, reason: Base cycle policy.}
                  complexity: hint
                """,
            "smelt.yml": """
                version: 2
                extends: base.yml
                project: {root_packages: [app]}
                rules:
                  complexity/excess-positional-parameters: {max: 3}
                  architecture/import-cycle: {severity: warning, reason: Local cycle policy.}
                  complexity: warning
                """,
            "app/__init__.py": "",
            "app/main.py": "",
        },
    )
    code, output, error = _run(
        capsys,
        "context",
        "app/main.py",
        "--config",
        str(root / "smelt.yml"),
        "--format",
        "json",
    )

    assert (code, error) == (0, "")
    rules = json.loads(output)["rule_details"]
    cycle = rules["architecture/import-cycle"]
    assert (cycle["severity"], cycle["source"], cycle["reason"]) == (
        "warning",
        "smelt.yml",
        "Local cycle policy.",
    )
    complexity = rules["complexity/excess-positional-parameters"]
    assert (complexity["severity"], complexity["source"]) == ("hint", "base.yml")


def test_an_explicit_base_path_widens_the_check_to_its_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy_project(
        tmp_path,
        {
            "base.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {tests/unbounded-wait: error}\n",
            "runtime/smelt.yml": "extends: ../base.yml\n",
            "runtime/app/__init__.py": "",
            "runtime/tests/test_wait.py": "import subprocess\ndef test_wait():\n    subprocess.run(['tool'], check=True)\n",
        },
    )
    code, output, error = _run(
        capsys,
        "check",
        str(tmp_path / "base.yml"),
        "--config",
        str(tmp_path / "runtime/smelt.yml"),
        "--format",
        "json",
    )
    report = json.loads(output)
    assert code == 1
    assert error == ""
    assert [v["code"] for v in report["violations"]] == ["TST008"]
    assert report["scope"]["paths"] == []


def test_a_member_check_runs_the_workspace_scoped_to_that_member(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_policy_project(
        tmp_path,
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*"]\n',
            "smelt.yml": "version: 2\nproject: {workspace: uv}\n"
            "rules: {visibility/internal-public-member: warning}\n",
            "services/api/smelt.yml": "rules: {visibility/internal-public-member: error}\n"
            "architecture: {shared: [api]}\n",
            "services/api/src/api/__init__.py": "",
            "services/api/src/api/views.py": "def helper(): return 1\n",
            "services/web/src/web/__init__.py": "",
            "services/web/src/web/views.py": "def helper(): return 1\n",
        },
    )
    explicit = _run(
        capsys,
        "check",
        "--config",
        str(tmp_path / "services/api/smelt.yml"),
        "--format",
        "json",
    )
    monkeypatch.chdir(tmp_path / "services/api")

    code, output, _ = _run(capsys, "check", "--format", "json")

    report = json.loads(output)
    assert code == 1
    assert report["scope"]["paths"] == ["services/api"]
    assert report["scope"]["totals"] == "workspace"
    assert report["coverage"]["scope"] == "workspace"
    assert [(v["path"], v["severity"]) for v in report["violations"]] == [
        ("services/api/src/api/views.py", "error")
    ]
    assert explicit == (code, output, "")
    _, output, _ = _run(capsys, "check")
    assert "Workspace policy coverage" in output
    assert "4 modules in workspace" in output


@pytest.mark.parametrize(
    ("member_config", "source", "rule_sources"),
    [
        (
            "architecture: {shared: [api]}\n",
            "services/api/smelt.yaml",
            ["smelt.yml", "services/api/smelt.yaml"],
        ),
        (
            "rules: {visibility/internal-public-member: warning}\n",
            None,
            ["smelt.yml", "services/api/smelt.yaml"],
        ),
        (
            "architecture:\n  packages:\n    api: {match: api, public: ['.']}\n",
            None,
            ["smelt.yml", "services/api/smelt.yaml"],
        ),
        (None, None, ["smelt.yml"]),
    ],
)
def test_context_names_the_governing_workspace_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    member_config: str | None,
    source: str | None,
    rule_sources: list[str],
) -> None:
    files = {
        "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*"]\n',
        "smelt.yml": "version: 2\nproject: {workspace: uv}\n",
        "services/api/src/api/__init__.py": "",
        "services/api/src/api/views.py": "",
    }
    if member_config is not None:
        files["services/api/smelt.yaml"] = member_config
    root = write_policy_project(tmp_path, files)
    args = ("context", "api.views", "--config", str(root / "smelt.yml"))
    code, output, error = _run(capsys, *args, "--format", "json")

    assert (code, error) == (0, "")
    brief = json.loads(output)
    assert (brief["member"], brief["architecture_source"], brief["rule_sources"]) == (
        "services/api",
        source,
        rule_sources,
    )
    _, output, _ = _run(capsys, *args)
    assert "Member: services/api" in output
    assert f"Architecture: {source or 'no local layout declared'}" in output
    assert f"Rules: {' < '.join(rule_sources)}" in output


def test_context_and_config_show_explain_member_style_and_rule_exceptions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "base.yml": "style: {future_annotations: when-needed}\n",
            "smelt.yml": """
                extends: base.yml
                version: 2
                project: {workspace: uv}
                rules:
                  complexity/excess-positional-parameters: {severity: warning, max: 3}
                  visibility/internal-public-member: warning
                """,
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*"]\n',
            "services/api/src/api/__init__.py": "",
            "services/api/src/api/legacy/__init__.py": "",
            "services/api/smelt.yaml": """
                rules:
                  complexity/excess-positional-parameters: {max: 5, reason: Preserve plugin callbacks.}
                style:
                  overrides:
                    legacy:
                      match: api
                      rules:
                        complexity/excess-positional-parameters: {max: 6, reason: Match generated driver calls.}
                        visibility/internal-public-member: {severity: hint, reason: Plugin entry points.}
                architecture:
                  packages:
                    legacy: {match: api, public: ['.']}
                """,
        },
    )
    config = str(root / "smelt.yml")
    args = ("context", "services/api/src/api/legacy/adapter.py", "--config", config)
    code, output, error = _run(capsys, *args, "--format", "json")
    assert (code, error) == (0, "")
    brief = json.loads(output)
    assert brief["style"]["future_annotations"]["source"] == "base.yml"
    option = brief["style"]["max_positional_parameters"]
    assert (option["value"], option["source"], option["reason"]) == (
        6,
        "services/api/smelt.yaml",
        "Match generated driver calls.",
    )
    assert (
        brief["rule_details"]["complexity/excess-positional-parameters"]["activation"]
        == "rules"
    )
    rule = brief["rule_details"]["visibility/internal-public-member"]
    assert (rule["severity"], rule["source"], rule["reason"]) == (
        "hint",
        "services/api/smelt.yaml",
        "Plugin entry points.",
    )
    _, output, _ = _run(capsys, *args)
    assert "Package API services/api:legacy" in output
    assert "Match generated driver calls." in output
    assert "Plugin entry points." in output

    code, output, error = _run(
        capsys, "config", "show", "--sources", "--config", config, "--format", "json"
    )
    assert (code, error) == (0, "")
    data = json.loads(output)
    assert "members" not in data["config"]
    assert "services/api" in data["workspace_members"]
    assert data["origins"][
        "members.services/api.style.overrides.legacy.rules.complexity/excess-positional-parameters.reason"
    ].endswith("services/api/smelt.yaml")


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def clean_project(tmp_path: Path) -> Path:
    return write_policy_project(
        tmp_path,
        {
            "smelt.yml": LAYERED_CONFIG,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/application/__init__.py": "",
            "app/application/service.py": "from app import domain\n",
        },
    )


def test_retired_keys_selectors_and_unknown_layers_share_one_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "smelt.yml"
    policy.write_text(
        "version: 2\nproject: {root_packages: [app]}\nstructure: {}\nrules: {import-cycle: error}\narchitecture:\n  layers: {api: {path: api, may_depend_on: [missing]}}\n",
        encoding="utf-8",
    )
    code, out, _ = _run(capsys, "--config", str(policy), "check", "--format", "json")
    assert code == 2
    issues = json.loads(out)["error"]["config"]["issues"]
    assert [issue["location"] for issue in issues] == [
        "structure",
        "rules.import-cycle",
        "architecture.layers.api.may_depend_on[0]",
    ]
    assert 'unknown layer "missing"' in issues[-1]["message"]


def test_missing_version_skips_dependent_config_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "smelt.yml"
    path.write_text(
        "rules: {import-cycle: error}\narchitecture: {layers: {one: {may_depend_on: [missing]}}}\n",
        encoding="utf-8",
    )
    code, out, _ = _run(capsys, "--config", str(path), "check", "--format", "json")
    assert code == 2
    assert [i["location"] for i in json.loads(out)["error"]["config"]["issues"]] == [
        "version"
    ]


class TestCheckCommand:
    def test_concise_keeps_the_finding_location_and_exit_threshold(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {tests/missing-outcome-check: warning}\n",
                "app/__init__.py": "",
                "tests/test_smoke.py": "def test_smoke(): pass\n",
            },
        )
        code, out, err = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--format",
            "concise",
            "--fail-on",
            "warning",
        )

        assert code == 1
        assert err == ""
        assert "tests/test_smoke.py:1:1 TST006" in out
        assert "Expected" not in out

    @pytest.mark.parametrize("fail_on", ["error", "warning"])
    @pytest.mark.parametrize(
        "output_format", ["json", "sarif", "github", "text", "concise"]
    )
    def test_configuration_warnings_are_structured_and_affect_the_threshold(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        fail_on: str,
        output_format: str,
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\narchitecture:\n  layers:\n    application: {path: application, may_depend_on: [infrastructure]}\n    infrastructure: {path: infrastructure, may_depend_on: [application]}",
                "app/__init__.py": "",
            },
        )
        code, out, err = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--format",
            output_format,
            "--fail-on",
            fail_on,
            "--select",
            "MET001",
        )
        assert code == (0 if fail_on == "error" else 1)
        assert "cyclic may_depend_on" in out
        assert not err
        if output_format == "json":
            report = json.loads(out)
            assert report["status"] == ("passed" if fail_on == "error" else "failed")
            assert report["summary"]["warnings"] == 1
            assert report["violations"] == []
            [diagnostic] = report["configuration_diagnostics"]
            assert diagnostic["key"] == "architecture.layers"
            assert diagnostic["path"] == "smelt.yml"
            assert diagnostic["severity"] == "warning"
        elif output_format == "sarif":
            [run] = json.loads(out)["runs"]
            [invocation] = run["invocations"]
            assert invocation["executionSuccessful"]
            [notification] = invocation["toolConfigurationNotifications"]
            assert notification["level"] == "warning"
            assert (
                notification["locations"][0]["physicalLocation"]["artifactLocation"][
                    "uri"
                ]
                == "smelt.yml"
            )

    def test_all_prefix_enables_cycle_check_and_explicit_off_wins(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\narchitecture: {shared: [app]}\n",
                "app/__init__.py": "",
                "app/one.py": "from app import two",
                "app/two.py": "from app import one",
            },
        )
        args = ("check", "--config", str(root / "smelt.yml"), "--format", "json")
        code, out, _ = _run(capsys, *args)
        assert code == 0
        assert json.loads(out)["violations"] == []
        code, out, _ = _run(capsys, *args, "--select", "ALL")
        assert code == 1
        assert "ARC004" in {item["code"] for item in json.loads(out)["violations"]}
        add_rules(root, {"ARC004": "off"})
        code, out, _ = _run(capsys, *args, "--select", "ALL")
        assert code == 0
        assert "ARC004" not in json.loads(out)["scope"]["rules"]

    def test_enabled_rule_debt_can_still_be_resolved_and_pruned(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {ARC004: error}\ndebt: .smelt/debt.json",
                "app/__init__.py": "",
            },
        )
        entry = DebtEntry("b" * 32, "ARC004", "app/old.py", "removed cycle")
        Debt([entry]).write(root / ".smelt/debt.json")
        assert check(root).resolved_debt == [entry]
        code, _, _ = _run(
            capsys, "debt", "--config", str(root / "smelt.yml"), "--prune"
        )
        assert code == 0
        assert Debt.load(root / ".smelt/debt.json").entries == []

    def test_minimal_configuration_reports_only_baseline_rules(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}",
                "app/__init__.py": "",
                "app/helpers.py": "def _hidden(): pass",
                "tests/test_smoke.py": "def test_smoke(): pass",
            },
        )
        code, out, _ = _run(
            capsys, "check", "--config", str(root / "smelt.yml"), "--format", "json"
        )
        report = json.loads(out)
        assert code == 0
        assert set(report["scope"]["rules"]) == {
            "ARC011",
            "MET001",
            "MET002",
            "MET003",
            "MET004",
        }
        assert report["violations"] == []

    @pytest.mark.parametrize("prune", [False, True])
    def test_inactive_rule_debt_is_preserved_when_recording_and_pruning(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, prune: bool
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\ndebt: .smelt/debt.json",
                "app/__init__.py": "",
            },
        )
        entry = DebtEntry(
            "a" * 32, "ARC004", "app/old.py", "cycle not currently checked"
        )
        Debt([entry]).write(root / ".smelt/debt.json")
        outcome = check(root)
        assert outcome.resolved_debt == []
        assert outcome.report.violations == []
        args = ("--prune",) if prune else ()
        code, _, _ = _run(capsys, "debt", "--config", str(root / "smelt.yml"), *args)
        assert code == 0
        assert Debt.load(root / ".smelt/debt.json").entries == [entry]

    def test_type_dispatch_is_a_hint_and_never_automatically_rewritten(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        source = "if isinstance(event, A): first(event)\nelif isinstance(event, B): second(event)\nelif isinstance(event, C): third(event)\n"
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nstyle:\n  overrides:\n    dispatch: {match: app, type_dispatch: {}}",
                "app/__init__.py": "",
                "app/events.py": source,
            },
        )
        before = (root / "app/events.py").read_bytes()
        code, out, _ = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--select",
            "IDM002",
            "--fix",
            "--format",
            "json",
        )
        assert code == 0
        assert json.loads(out)["violations"][0]["code"] == "IDM002"
        assert json.loads(out)["violations"][0]["severity"] == "hint"
        assert (root / "app/events.py").read_bytes() == before

    def test_instance_assignment_check_never_automatically_splits_a_swap(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        source = "class Inbox:\n    def update(self):\n        self.a, self.b = self.b, self.a\n"
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject:\n  root_packages:\n  - app\nstyle:\n  overrides:\n    fields:\n      match: app\n      rules:\n        idioms/bundled-instance-assignment: error\n",
                "app/__init__.py": "",
                "app/service.py": source,
            },
        )
        before = (root / "app/service.py").read_bytes()
        code, out, _ = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--select",
            "IDM001",
            "--fix",
            "--format",
            "json",
        )
        assert code == 1
        assert json.loads(out)["violations"][0]["code"] == "IDM001"
        assert (root / "app/service.py").read_bytes() == before

    def test_test_quality_rule_reports_json_and_sarif(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject:\n  root_packages:\n  - app\nrules:\n  tests/string-patch-target: error\n",
                "app/__init__.py": "",
                "tests/test_service.py": "from unittest.mock import patch\ndef test_run(): patch('app.repo')",
            },
        )
        arguments = (
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--select",
            "TST004",
        )
        code, out, _ = _run(capsys, *arguments, "--format", "json")
        assert code == 1
        report = json.loads(out)
        assert report["violations"][0]["code"] == "TST004"
        assert "tests" not in report["coverage"]
        assert "TST004" in report["scope"]["rules"]
        code, out, _ = _run(capsys, *arguments, "--format", "sarif")
        assert code == 1
        assert json.loads(out)["runs"][0]["results"][0]["ruleId"] == "TST004"

    def test_ordering_fixes_require_explicit_request_and_report_result(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "\nversion: 2\nproject:\n  root_packages:\n  - app\narchitecture:\n  shared:\n  - app\nstyle:\n  overrides:\n    order:\n      match: app\n      ordering: {}\n      rules:\n        layout/definition-order: error\n",
                "app/service.py": "def _helper(): return 7\n\ndef api(): return _helper()\n",
            },
        )
        source = root / "app/service.py"
        original = source.read_bytes()
        arguments = (
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--select",
            "LAY004",
            "--format",
            "json",
        )

        code, _, _ = _run(capsys, *arguments)
        assert code == 1
        assert source.read_bytes() == original

        code, out, err = _run(capsys, *arguments, "--fix")
        report = json.loads(out)

        assert code == 0
        assert err == ""
        assert report["fixes"]["ordering"]["files"] == ["app/service.py"]
        assert source.read_text().lstrip().startswith("def api")

        code, out, _ = _run(capsys, *arguments, "--fix")
        assert code == 0
        assert json.loads(out)["fixes"]["ordering"]["files"] == []

    def test_private_module_name_has_a_machine_readable_finding(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": """
                version: 2
                project: {root_packages: [app]}
                style:
                  overrides:
                    names: {match: app, private_modules: forbid}
            """,
                "app/_service.py": "__all__ = ['run']\ndef run(): pass\n",
            },
        )

        code, out, err = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--select",
            "LAY003",
            "--show-coverage",
            "--format",
            "json",
        )
        document = json.loads(out)

        assert code == 1
        assert err == ""
        assert document["violations"][0]["code"] == "LAY003"
        assert document["violations"][0]["path"] == "app/_service.py"
        assert document["coverage"]["contracts"][0]["private_modules"] == "forbid"

    def test_header_policy_and_version_are_visible_to_agents(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": """
                version: 2
                project: {root_packages: [app], python_version: "3.14"}
                style:
                  overrides:
                    headers: {match: app, module_docstrings: forbid, future_annotations: when-needed}
            """,
                "app/source.py": '"""Module introduction."""\nfrom __future__ import annotations\n',
            },
        )

        code, out, err = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--show-coverage",
            "--format",
            "json",
        )
        document = json.loads(out)

        assert code == 1
        assert err == ""
        assert {"LAY002", "TYP001"} <= {item["code"] for item in document["violations"]}
        assert document["coverage"]["python_targets"][0]["minimum"] == "3.14"

        code, out, err = _run(
            capsys,
            "context",
            "app/source.py",
            "--config",
            str(root / "smelt.yml"),
            "--format",
            "json",
        )
        context = json.loads(out)

        assert code == 0
        assert err == ""
        assert context["python_target"]["minimum"] == "3.14"
        assert context["header_policy"]["future_annotations"]["policy"] == "when-needed"

    def test_contract_failure_and_coverage_metadata_are_machine_readable(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": """
                version: 2
                project: {root_packages: [library]}
                architecture:
                  packages:
                    public-api: {match: library, required: [api], public: [api]}
            """,
                "library/__init__.py": "",
                "library/private.py": "",
                "library/client.py": "",
            },
        )

        code, out, err = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--select",
            "ARC014",
            "--show-coverage",
            "--format",
            "json",
        )
        document = json.loads(out)

        assert code == 1
        assert err == ""
        assert document["violations"][0]["code"] == "ARC014"
        assert document["coverage"]["contracts"][0]["missing"] == ["library.api"]

    def test_strict_coverage_override_and_optional_inventory(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": LAYERED_CONFIG,
                "app/__init__.py": "",
                "app/domain/model.py": "VALUE = 1\n",
                "client/api.py": "VALUE = 1\n",
            },
        )

        code, out, err = _run(
            capsys,
            "check",
            "--config",
            str(root / "smelt.yml"),
            "--strict-coverage",
            "--show-coverage",
            "--format",
            "json",
        )

        document = json.loads(out)
        assert code == 1
        assert err == ""
        assert document["coverage"]["mode"] == "strict"
        assert document["coverage"]["complete"] is False
        assert document["violations"][0]["code"] == "ARC013"
        assert any(
            item["path"] == "client/api.py"
            for item in document["coverage"]["inventory"]
        )
        assert "coverage" not in (root / "smelt.yml").read_text(encoding="utf-8")

    @pytest.mark.parametrize("prune", [False, True])
    def test_debt_command_does_not_write_when_coverage_is_incomplete(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, prune: bool
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": LAYERED_CONFIG.replace(
                    "rules: {", "rules: {architecture/uncovered-source: error, "
                ),
                "app/__init__.py": "",
                "client/api.py": "VALUE = 1\n",
            },
        )
        before = (root / "smelt.yml").read_bytes()
        args = ["debt", "--config", str(root / "smelt.yml")]
        if prune:
            args.append("--prune")

        code, _, err = _run(capsys, *args)

        assert code == 2
        assert "coverage cannot be accepted as debt" in err
        assert not (root / ".smelt").exists()
        assert (root / "smelt.yml").read_bytes() == before

    @pytest.mark.parametrize("output_format", ["json", "sarif", "github"])
    def test_nested_configuration_identifies_and_rebases_paths(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, output_format: str
    ) -> None:
        write_policy_project(
            tmp_path,
            {
                ".git": "",
                "service/smelt.yml": LAYERED_CONFIG,
                "service/app/__init__.py": "",
                "service/app/domain/model.py": "from app.infra import db\n",
                "service/app/infra/db.py": "",
            },
        )

        code, out, err = _run(
            capsys,
            "check",
            "--config",
            str(tmp_path / "service/smelt.yml"),
            "--format",
            output_format,
        )

        assert code == 1
        assert err == ""
        if output_format == "json":
            document = json.loads(out)
            assert document["project"]["root"] == (tmp_path / "service").as_posix()
            assert document["project"]["repository_root"] == tmp_path.as_posix()
            assert document["project"]["repository_prefix"] == "service"
            assert (
                document["project"]["config"]
                == (tmp_path / "service/smelt.yml").as_posix()
            )
            assert document["violations"][0]["path"] == "app/domain/model.py"
        elif output_format == "sarif":
            location = json.loads(out)["runs"][0]["results"][0]["locations"][0]
            assert (
                location["physicalLocation"]["artifactLocation"]["uri"]
                == "service/app/domain/model.py"
            )
        else:
            assert "file=service/app/domain/model.py," in out

    def test_public_hook_checks_incoming_edges_and_unrelated_python_files(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            clean_project,
            {
                "app/domain/model.py": "from app.application import service\n",
                "scripts/build.py": "print('build')\n",
            },
        )
        manifest = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / ".pre-commit-hooks.yaml").read_text(
                encoding="utf-8"
            )
        )[0]
        args = manifest["entry"].split()[1:]
        if manifest.get("pass_filenames", True):
            args.extend(["app/application/service.py", "scripts/build.py"])
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, *args)

        assert code == 1
        assert "ARC001" in out
        assert "app/domain/model.py" in out
        assert err == ""

    def test_unclassified_package_warnings_are_grouped_only_in_text(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            clean_project,
            {
                "app/platform/__init__.py": "",
                "app/platform/a.py": "",
                "app/platform/b.py": "",
            },
        )
        monkeypatch.chdir(clean_project)

        _, text, _ = _run(capsys, "check", "--select", "ARC012")
        _, output, _ = _run(capsys, "check", "--select", "ARC012", "--format", "json")
        _, statistics, _ = _run(capsys, "check", "--select", "ARC012", "--statistics")

        assert text.count("ARC012 architecture/unclassified-module") == 1
        assert "2 modules have no architecture classification" in text
        assert "Give app.platform a layer" in text
        assert len(json.loads(output)["violations"]) == 2
        assert "2  ARC012 architecture/unclassified-module" in statistics

    @pytest.mark.parametrize("path", ["app/domian", "README.md"])
    def test_rejects_missing_or_unanalyzed_scope(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        path: str,
    ) -> None:
        (clean_project / "README.md").write_text("documentation", encoding="utf-8")
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, "check", path, "--format", "json")

        error = json.loads(out)["error"]
        assert code == 2
        assert err == ""
        assert error["kind"] == "usage"
        assert error["input"] == {"option": "paths", "value": path}
        assert (
            "does not exist" in error["message"]
            or "no analyzed Python" in error["message"]
        )

    @pytest.mark.parametrize("option", ["--select", "--ignore"])
    def test_rejects_unknown_selector(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        option: str,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, "check", option, "ARC,SMT999")

        assert code == 2
        assert out == ""
        assert option in err
        assert 'unknown rule prefix "SMT999"' in err
        assert "MET003" not in err

    def test_usage_error_is_json_when_json_was_asked_for(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, "check", "--format", "json", "--select", "SMT999")

        document = json.loads(out)
        assert code == 2
        assert err == ""
        assert document["schema_version"] == 2
        assert document["status"] == "error"
        assert document["error"]["kind"] == "usage"
        assert document["error"]["input"] == {"option": "--select", "value": "SMT999"}
        assert 'unknown rule prefix "SMT999"' in document["error"]["message"]

    @pytest.mark.parametrize(
        ("args", "given"),
        [
            (
                ["--format", "json", "--fail-on", "bogus"],
                {"option": "--fail-on", "value": "bogus"},
            ),
            (["--format=json", "--selct", "ARC"], None),
        ],
    )
    def test_argument_error_is_json_when_json_was_asked_for(
        self,
        capsys: pytest.CaptureFixture[str],
        args: list[str],
        given: dict[str, str] | None,
    ) -> None:
        code, out, err = _run(capsys, "check", *args)

        error = json.loads(out)["error"]
        assert code == 2
        assert err == ""
        assert error["kind"] == "usage"
        assert error["input"] == given

    @pytest.mark.parametrize("fmt", ["text", "xml"])
    def test_argument_error_stays_argparse_text_otherwise(
        self, capsys: pytest.CaptureFixture[str], fmt: str
    ) -> None:
        code, out, err = _run(capsys, "check", "--format", fmt, "--fail-on", "bogus")

        assert code == 2
        assert out == ""
        assert err.startswith("usage: smelt check")
        assert "smelt check: error: argument" in err

    def test_scope_identifies_files_in_json_and_text(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, out, _ = _run(capsys, "check", "app/application", "--format", "json")

        assert code == 0
        scope = json.loads(out)["scope"]
        assert scope["paths"] == ["app/application"]
        assert scope["files"] == 2
        assert "ARC001" in scope["rules"]
        assert (
            "Scope: app/application (2 analyzed source/test files)"
            in _run(capsys, "check", "app/application")[1]
        )

    @pytest.mark.parametrize("args", [("check",), ("config", "show")])
    def test_config_option_after_subcommand(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        args: tuple[str, ...],
    ) -> None:
        code, _, err = _run(capsys, *args, "--config", str(clean_project / "smelt.yml"))

        assert (code, err) == (0, "")

    def test_exit_code_one_on_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(
            capsys, "--config", str(GATEWAY / "smelt.yml"), "check", "--no-color"
        )

        assert code == 1
        assert out.splitlines()[-1] == "✗ 7 errors · 0 warnings · 0 hints · 21 modules"

    def test_exit_code_zero_when_clean(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, out, _ = _run(capsys, "check")

        assert code == 0
        assert out == (
            "Policy coverage: complete (configured); 0 uncovered, 0 unclassified sources\n"
            "✓ architecture · 4 modules\n"
        )

    def test_json_document(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(
            capsys, "--config", str(GATEWAY / "smelt.yml"), "check", "--format", "json"
        )

        document = json.loads(out)
        assert code == 1
        assert document["schema_version"] == 2
        assert document["status"] == "failed"
        assert document["summary"]["errors"] == 7
        assert document["violations"][0]["code"] == "ARC004"

    def test_select_narrows_rules(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = _run(
            capsys,
            "--config",
            str(GATEWAY / "smelt.yml"),
            "check",
            "--format",
            "json",
            "--select",
            "ARC005",
        )

        assert [v["code"] for v in json.loads(out)["violations"]] == ["ARC005"]

    def test_positional_paths_filter_results(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(GATEWAY)

        _, out, _ = _run(
            capsys, "check", "--format", "json", "src/gateway/shared/clock.py"
        )

        assert [v["path"] for v in json.loads(out)["violations"]] == [
            "src/gateway/shared/clock.py"
        ]

    def test_config_path_widens_the_scope_to_the_project(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(GATEWAY)

        _, out, _ = _run(
            capsys,
            "check",
            "--format",
            "json",
            "src/gateway/shared/clock.py",
            "smelt.yml",
        )

        paths = {v["path"] for v in json.loads(out)["violations"]}
        assert "src/gateway/shared/clock.py" in paths
        assert len(paths) > 1

    def test_config_error_exits_with_two(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        (tmp_path / "smelt.yml").write_text(
            "version: 2\nproject: {root_packages: [app]}\nbogus: 1\n"
        )

        code, _, err = _run(capsys, "--config", str(tmp_path / "smelt.yml"), "check")

        assert code == 2
        assert 'bogus: unknown key "bogus"' in err

    @pytest.mark.parametrize("input_file", ["smelt.yml", "debt.json", "app/model.py"])
    def test_invalid_encoding_is_a_structured_input_error(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, input_file: str
    ) -> None:
        root = write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\ndebt: debt.json\n",
                "app/__init__.py": "",
                "app/model.py": "VALUE = 1\n",
                "debt.json": '{"version":1,"violations":[]}',
            },
        )
        (root / input_file).write_bytes(b"# invalid encoding \xff\n")

        code, out, err = _run(
            capsys, "check", "--config", str(root / "smelt.yml"), "--format", "json"
        )

        assert code == 2
        assert json.loads(out)["status"] == "error"
        assert err == ""

    def test_config_error_is_json_with_its_location(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        (tmp_path / "smelt.yml").write_text(
            "version: 2\nproject: {root_packages: [app]}\nbogus: 1\n"
        )

        code, out, _ = _run(
            capsys,
            "--config",
            str(tmp_path / "smelt.yml"),
            "check",
            "--format",
            "json",
        )

        error = json.loads(out)["error"]
        assert code == 2
        assert error["kind"] == "config"
        assert error["message"] == "invalid configuration in smelt.yml"
        assert error["config"]["file"] == "smelt.yml"
        assert {"location": "bogus", "message": 'unknown key "bogus"'} in error[
            "config"
        ]["issues"]

    def test_missing_config_exits_with_two(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        code, _, err = _run(capsys, "check")

        assert code == 2
        assert "no smelt.yml found" in err

        code, out, _ = _run(capsys, "check", "--format", "json")

        error = json.loads(out)["error"]
        assert code == 2
        assert error["kind"] == "config"
        assert "no smelt.yml found" in error["message"]
        assert error["input"] is None

    def test_missing_root_package_exits_with_two(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        (tmp_path / "smelt.yml").write_text(
            "version: 2\nproject: {root_packages: [nothere]}\n"
        )

        code, _, err = _run(capsys, "--config", str(tmp_path / "smelt.yml"), "check")

        assert code == 2
        assert 'package "nothere" is in none of the source roots (.)' in err


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
class TestChangedHints:
    @pytest.fixture
    def repo(self, clean_project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            ["commit", "-qm", "i"],
        ):
            subprocess.run([*git, *args], cwd=clean_project, check=True)  # noqa: S603
        add_rules(clean_project, {"ARC012": "hint"})
        (clean_project / "app" / "misc.py").write_text("", encoding="utf-8")
        monkeypatch.chdir(clean_project)
        return clean_project

    @pytest.mark.usefixtures("repo")
    def test_changed_mode_shows_hints(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = _run(capsys, "check", "--changed", "--no-color")

        assert "app/misc.py  ARC012 architecture/unclassified-module  [hint]" in out

    @pytest.mark.usefixtures("repo")
    def test_full_check_folds_hints(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = _run(capsys, "check", "--no-color")

        assert "ARC012" not in out
        assert "1 hint (use --show-hints)" in out


class TestInfoCommands:
    @pytest.mark.parametrize("format_name", ["yaml", "json"])
    def test_config_show_roundtrips_feature_exceptions(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        format_name: str,
    ) -> None:
        root = tmp_path / "gateway"
        shutil.copytree(GATEWAY, root)
        path = root / "smelt.yml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["architecture"]["cross_feature"]["allow"].append(
            {"from": "voice.application", "to": "billing.application"}
        )
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        before = load_config(path).config

        code, out, err = _run(
            capsys, "config", "show", "--config", str(path), "--format", format_name
        )
        path.write_text(out, encoding="utf-8")

        assert (code, err) == (0, "")
        assert load_config(path).config == before

    def test_rules_json_lists_codes(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(capsys, "rules", "--format", "json")

        codes = [rule["code"] for rule in json.loads(out)]
        assert code == 0
        assert codes[:6] == ["ARC001", "ARC002", "ARC003", "ARC004", "ARC005", "ARC006"]

    def test_catalog_and_explanation_share_config_metadata(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out, _ = _run(capsys, "rules", "--format", "json")
        assert code == 0
        entry = next(rule for rule in json.loads(out) if rule["code"] == "IMP003")
        assert entry["requires"] == ["syntax"]
        assert "style.overrides" in entry["config"]

        code, out, _ = _run(capsys, "explain", "IMP003", "--format", "json")
        assert code == 0
        explanation = json.loads(out)
        assert explanation["requires"] == entry["requires"]
        assert explanation["config"] == entry["config"]

    def test_explain_by_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(capsys, "explain", "architecture/layer-boundary")

        assert code == 0
        assert out.startswith(
            "ARC001 architecture/layer-boundary  [architecture, error]\n"
        )

    def test_explain_unknown_rule(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, _, err = _run(capsys, "explain", "SMT10")

        assert code == 2
        assert 'unknown rule "SMT10"' in err

        code, out, _ = _run(capsys, "explain", "SMT10", "--format", "json")

        assert code == 2
        assert json.loads(out)["error"]["input"] == {"option": "rule", "value": "SMT10"}

    @pytest.mark.parametrize(
        ("raw", "suggestion"),
        [
            ("ARC999", ""),
            ("ARC002x", ""),
            ("ARC018", ""),  # Ambiguous between ARC008 and ARC010-ARC014.
            ("TST001", None),
            ("XRC001", ' (did you mean "ARC001"?)'),
            ("layer-boundry", ' (did you mean "architecture/layer-boundary"?)'),
        ],
    )
    def test_explain_suggests_only_close_rules(
        self, capsys: pytest.CaptureFixture[str], raw: str, suggestion: str | None
    ) -> None:
        code, _, err = _run(capsys, "explain", raw)

        if suggestion is None:
            assert code == 0
            return
        assert err.strip() == (
            f'error: unknown rule "{raw}"{suggestion}; `smelt rules` lists them all'
        )

    def test_config_schema_is_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(capsys, "config", "schema")

        assert code == 0
        assert json.loads(out)["title"] == "smelt.yml"

    def test_config_show_fills_defaults(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out, _ = _run(
            capsys,
            "--config",
            str(GATEWAY / "smelt.yml"),
            "config",
            "show",
            "--format",
            "json",
        )

        data = json.loads(out)
        assert code == 0
        assert data["architecture"]["import_graph"] == {
            "type_checking": "include",
            "transitive": False,
            "cycles": ["features", "layers", "siblings"],
            "type_checking_cycles": "include",
        }


class TestDiscoveryCommands:
    def test_context_for_planned_file_and_dotted_module(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, out, err = _run(
            capsys, "context", "app/domain/new_entity.py", "--format", "json"
        )

        data = json.loads(out)
        assert (code, err) == (0, "")
        assert (data["module"], data["layer"], data["planned"]) == (
            "app.domain.new_entity",
            "domain",
            True,
        )
        code, out, err = _run(
            capsys, "context", "app.application.service", "--format", "json"
        )
        assert (code, err) == (0, "")
        assert json.loads(out)["module"] == "app.application.service"

    def test_context_reuses_check_after_a_layer_violation(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = clean_project / "app/domain/order.py"
        module.write_text("from app.application import service\n", encoding="utf-8")
        monkeypatch.chdir(clean_project)
        code, report, err = _run(capsys, "check", "--format", "json")
        assert (code, err) == (1, "")
        assert "ARC001" in {v["code"] for v in json.loads(report)["violations"]}

        def unexpected_build(*args: object, **kwargs: object) -> None:
            pytest.fail("context rebuilt the import graph after an unchanged check")

        with monkeypatch.context() as warm:
            warm.setattr(ImportIndex, "build", unexpected_build)
            code, output, err = _run(
                capsys, "context", "app.domain.order", "--format", "json"
            )
        assert (code, err) == (0, "")
        assert "ARC001" in json.loads(output)["violations"]["codes"]

        module.write_text("", encoding="utf-8")
        code, output, err = _run(
            capsys, "context", "app.domain.order", "--format", "json"
        )
        assert (code, err) == (0, "")
        assert "ARC001" not in json.loads(output)["violations"]["codes"]

    def test_context_survives_unrelated_syntax_error(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (clean_project / "app/application/broken.py").write_text(
            "def broken(:\n", encoding="utf-8"
        )
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, "context", "app.domain", "--format", "json")

        data = json.loads(out)
        assert (code, err) == (0, "")
        assert data["layer"] == "domain"
        assert data["violations"] is None
        assert "broken.py" in data["analysis_error"]
        assert (
            "Current violations: unavailable"
            in _run(capsys, "context", "app.domain")[1]
        )

    def test_context_for_feature(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(
            capsys, "--config", str(GATEWAY / "smelt.yml"), "context", "voice"
        )

        assert code == 0
        assert out.startswith("Feature: voice   (gateway.features.voice)\n")

    def test_context_path_relative_to_cwd(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(GATEWAY / "src" / "gateway")

        code, out, _ = _run(capsys, "context", "shared", "--format", "json")

        assert code == 0
        assert json.loads(out)["module"] == "gateway.shared"

    def test_context_unknown_target(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, _, err = _run(
            capsys, "--config", str(GATEWAY / "smelt.yml"), "context", "voic"
        )

        assert code == 2
        assert 'did you mean "voice"?' in err


class TestInitCommand:
    def test_init_preserves_an_unrelated_editor_schema(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            tmp_path,
            {
                "app/__init__.py": "",
                ".smelt.schema.json": '{"title": "my custom schema"}\n',
            },
        )
        monkeypatch.chdir(tmp_path)

        code, _, err = _run(capsys, "init", "--maintainability")

        assert code == 2
        assert "was not generated by Smelt" in err
        assert not (tmp_path / "smelt.yml").exists()
        assert (
            json.loads((tmp_path / ".smelt.schema.json").read_text())["title"]
            == "my custom schema"
        )

    @pytest.mark.parametrize("source_root", [".", "src"])
    def test_explicit_maintainability_rules_checks_a_small_project_without_layers(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        source_root: str,
    ) -> None:
        source = "def _helper(): pass\ndef api(): _helper()\n"
        module = f"{source_root}/app/api.py"
        write_policy_project(
            tmp_path, {f"{source_root}/app/__init__.py": "", module: source}
        )
        monkeypatch.chdir(tmp_path)

        code, out, err = _run(capsys, "init", "--maintainability")

        assert code == 0
        assert err == ""
        assert "editor completion" in out
        written = (tmp_path / "smelt.yml").read_text(encoding="utf-8")
        assert "preset:" not in written
        assert "visibility/internal-public-member: warning" in written
        assert "architecture:" not in written
        schema_path = tmp_path / ".smelt.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        properties = schema["$defs"]["RuleSettings"]["properties"]
        assert "visibility/internal-public-member" in properties
        assert (
            "class Browser"
            in properties["visibility/internal-public-member"]["markdownDescription"]
        )
        assert written.startswith(
            "# yaml-language-server: $schema=.smelt.schema.json\n"
        )
        assert (tmp_path / module).read_text(encoding="utf-8") == source

        # Editor artifacts do not participate in analysis or rule activation.
        schema_path.write_text("not a schema", encoding="utf-8")

        code, out, err = _run(capsys, "check", "--format", "json")

        assert code == 1
        assert err == ""
        report = json.loads(out)
        assert {"LAY004", "VIS001"} <= {
            finding["code"] for finding in report["violations"]
        }

    def test_explicit_maintainability_rules_keeps_workspace_and_layer_inference(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            tmp_path,
            {
                "pyproject.toml": '[tool.uv.workspace]\nmembers = ["backend", "client"]\n',
                "backend/src/app/__init__.py": "",
                "backend/src/app/domain/__init__.py": "",
                "backend/src/app/infra/__init__.py": "",
                "backend/src/app/domain/model.py": "from app.infra import db\n",
                "backend/src/app/infra/db.py": "",
                "client/src/client/__init__.py": "",
                "client/src/client/api.py": "def _helper(): pass\ndef api(): _helper()\n",
            },
        )
        monkeypatch.chdir(tmp_path)

        code, _, err = _run(capsys, "init", "--maintainability")

        assert code == 0
        assert err == ""
        config = load_config(tmp_path / "smelt.yml").config
        assert config.project.root_packages == ["app", "client"]
        assert config.architecture.layers == {}
        architecture = config.members["backend"].architecture
        assert architecture is not None
        assert set(architecture.layers) == {"domain", "infrastructure"}
        assert config.tests.layout == "none"
        assert {"ARC001", "LAY004", "VIS001"} <= {
            v.code for v in check(tmp_path).report.violations
        }

    def test_writes_inferred_config_and_reports_violations(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            tmp_path,
            {
                "app/__init__.py": "",
                "app/domain/__init__.py": "",
                "app/domain/model.py": "from app.infra import db\n",
                "app/infra/__init__.py": "",
                "app/infra/db.py": "",
            },
        )
        monkeypatch.chdir(tmp_path)

        code, out, _ = _run(capsys, "init")

        assert code == 0
        assert (tmp_path / "smelt.yml").is_file()
        assert "  layers: domain (domain), infrastructure (infra)\n" in out
        assert "The inferred config yields 1 error and 0 warnings." in out

    def test_summary_accounts_for_every_workspace_member(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            tmp_path,
            {
                "pyproject.toml": (
                    "[tool.uv.workspace]\n"
                    'members = ["api", "e2e", "web", "old"]\n'
                    'exclude = ["old"]\n'
                ),
                "api/src/api/__init__.py": "",
                "e2e/src/stack/server/__init__.py": "",
                "web/index.ts": "",
                "old/src/old/__init__.py": "",
            },
        )
        monkeypatch.chdir(tmp_path)

        code, out, _ = _run(capsys, "init")

        assert code == 0
        assert (
            "  workspace members:\n"
            "    api: api in api/src\n"
            "    e2e: stack (namespace package) in e2e/src\n"
            "    old: skipped, excluded by tool.uv.workspace.exclude\n"
            "    web: skipped, no Python package in src/ or the member directory\n"
        ) in out

    def test_refuses_to_overwrite(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, _, err = _run(capsys, "init")

        assert code == 2
        assert "smelt.yml already exists (use --force to overwrite)" in err

    @pytest.mark.parametrize("before", [True, False])
    def test_custom_config_is_the_only_file_written(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        before: bool,
    ) -> None:
        reviewed = (clean_project / "smelt.yml").read_text(encoding="utf-8")
        monkeypatch.chdir(clean_project)
        args = ["init", "--config", "custom.yml"]
        if before:
            args = ["--config", "custom.yml", "init"]

        code, out, err = _run(capsys, *args)

        assert (code, err) == (0, "")
        assert out.startswith("Wrote custom.yml\n")
        assert "pass --config custom.yml to every command" in out
        assert load_config(clean_project / "custom.yml").config.project.root_packages
        assert (clean_project / "smelt.yml").read_text(encoding="utf-8") == reviewed

    def test_existing_custom_config_needs_force_and_leaves_the_default_alone(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reviewed = (clean_project / "smelt.yml").read_text(encoding="utf-8")
        (clean_project / "custom.yml").write_text("# mine\n", encoding="utf-8")
        monkeypatch.chdir(clean_project)

        refused, _, err = _run(capsys, "init", "--config", "custom.yml")
        forced, _, _ = _run(capsys, "init", "--force", "--config", "custom.yml")

        assert refused == 2
        assert "custom.yml already exists (use --force to overwrite)" in err
        assert forced == 0
        assert "# mine" not in (clean_project / "custom.yml").read_text(
            encoding="utf-8"
        )
        assert (clean_project / "smelt.yml").read_text(encoding="utf-8") == reviewed

    def test_custom_config_in_a_subdirectory_infers_that_project(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            tmp_path, {"service/shop/__init__.py": "", "other/x.py": ""}
        )
        monkeypatch.chdir(tmp_path)

        code, out, _ = _run(capsys, "init", "--config", "service/smelt.yml")

        assert code == 0
        assert out.startswith("Wrote service/smelt.yml\n")
        assert "every command" not in out
        loaded = load_config(tmp_path / "service" / "smelt.yml")
        assert loaded.config.project.root_packages == ["shop"]
        assert not (tmp_path / "smelt.yml").exists()

    @pytest.mark.parametrize("filename", ["smelt.yml", "smelt.yaml"])
    def test_force_overwrites_an_existing_config_in_place(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        filename: str,
    ) -> None:
        if filename != "smelt.yml":
            (clean_project / "smelt.yml").rename(clean_project / filename)
        monkeypatch.chdir(clean_project)

        code, out, _ = _run(capsys, "init", "--force")

        assert code == 0
        assert out.startswith(f"Wrote {filename}\n")
        assert (clean_project / filename).is_file()
        other = "smelt.yaml" if filename == "smelt.yml" else "smelt.yml"
        assert not (clean_project / other).exists()


class TestDebtCommand:
    def test_pruning_retains_debt_in_a_disabled_package(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_policy_project(
            tmp_path,
            {
                "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {visibility/internal-public-member: warning}\n",
                "app/__init__.py": "",
                "app/api.py": "def helper(): pass\n",
                "app/legacy/__init__.py": "",
                "app/legacy/api.py": "def helper(): pass\n",
            },
        )
        monkeypatch.chdir(tmp_path)
        code, _, err = _run(capsys, "debt")
        assert code == 0
        assert not err
        config = tmp_path / "smelt.yml"
        with config.open("a", encoding="utf-8") as target:
            target.write(
                "style:\n  overrides:\n    legacy:\n      match: app.legacy\n      rules: {visibility/internal-public-member: off}\n"
            )

        code, _, err = _run(capsys, "debt", "--prune")

        assert code == 0
        assert not err
        assert len(Debt.load(tmp_path / ".smelt/debt.json").entries) == 2

    def test_prune_upgrades_legacy_debt_without_accepting_new_violations(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = _violating_project()
        files["smelt.yml"] += "debt: debt.json\n"
        root = write_policy_project(tmp_path, files)
        [known] = check(root).unfiltered
        source = files["app/domain/model.py"]
        legacy = fingerprint(known, source, legacy=True)
        Debt([DebtEntry(legacy, known.code, known.path, known.message)]).write(
            root / "debt.json"
        )
        write_policy_project(root, {"app/domain/new.py": source})
        monkeypatch.chdir(root)

        code, _, err = _run(capsys, "debt", "--prune")

        assert (code, err) == (0, "")
        debt = Debt.load(root / "debt.json")
        assert len(debt.entries) == 1
        assert debt.entries[0].fingerprint == fingerprint(known, source)
        assert debt.entries[0].fingerprint != legacy
        write_policy_project(
            root, {"app/domain/model.py": source.rstrip() + " # changed comment\n"}
        )
        report = check(root).report
        assert report.in_debt == 1
        assert [v.path for v in report.violations] == ["app/domain/new.py"]

    @pytest.mark.parametrize(
        "content",
        ['{"version":1,"violations":[{}]}', '{"version":3,"violations":[]}', "{broken"],
    )
    def test_malformed_debt_is_a_config_error(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        content: str,
    ) -> None:
        config = clean_project / "smelt.yml"
        config.write_text(
            config.read_text(encoding="utf-8") + "debt: debt.json\n", encoding="utf-8"
        )
        (clean_project / "debt.json").write_text(content, encoding="utf-8")
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, "check")

        assert code == 2
        assert out == ""
        assert "debt.json" in err
        assert "Traceback" not in err

        code, out, _ = _run(capsys, "check", "--format", "json")

        assert code == 2
        assert json.loads(out)["error"]["config"]["file"].endswith("debt.json")

    def test_writes_default_debt_and_check_uses_it(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = write_policy_project(tmp_path, _violating_project())
        monkeypatch.chdir(root)

        code, out, err = _run(capsys, "debt")

        assert (code, err) == (0, "")
        assert out == (
            "Wrote .smelt/debt.json (1 violation, 1 matching entry)\n"
            "Added `debt: .smelt/debt.json` to smelt.yml\n"
        )
        assert _run(capsys, "check")[0] == 0

    def test_uncomments_the_debt_line_from_init(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = _violating_project()
        files["smelt.yml"] += "# debt: .smelt/debt.json\n"
        root = write_policy_project(tmp_path, files)
        monkeypatch.chdir(root)

        _run(capsys, "debt")

        config = (root / "smelt.yml").read_text(encoding="utf-8")
        assert config.endswith("\ndebt: .smelt/debt.json\n")
        assert "# debt" not in config

    def test_prune_removes_fixed_entries(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = _violating_project()
        files["smelt.yml"] += "debt: debt.json\n"
        root = write_policy_project(tmp_path, files)
        monkeypatch.chdir(root)
        _run(capsys, "debt")
        (root / "app/domain/model.py").write_text("", encoding="utf-8")

        code, out, err = _run(capsys, "debt", "--prune")

        assert code == 0
        assert out == "Removed 1 resolved entry from debt.json (0 remain)\n"
        assert err == ""
        data = json.loads((root / "debt.json").read_text(encoding="utf-8"))
        assert data["violations"] == []


def _violating_project() -> dict[str, str]:
    return {
        "smelt.yml": LAYERED_CONFIG,
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/model.py": "from app.application import service\n",
        "app/application/__init__.py": "",
        "app/application/service.py": "",
    }


def _python(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


OPTIONAL_DEFAULT_SOURCE = (
    "def create(options: int | None = None):\n    return options or 7\n"
)


@pytest.mark.parametrize(
    "flags", [(), ("--unsafe-fixes",), ("--fix",), ("--fix", "--unsafe-fixes")]
)
def test_optional_default_cli_requires_both_fix_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: tuple[str, ...]
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/service.py": OPTIONAL_DEFAULT_SOURCE,
        },
    )
    arguments = [
        "check",
        "--config",
        str(root / "smelt.yml"),
        "--select",
        "TYP003",
        "--fail-on",
        "warning",
        "--format",
        "json",
    ]
    code = main([*arguments, *flags])
    output = capsys.readouterr()
    assert output.err == ""
    report = json.loads(output.out)
    if flags == ("--fix", "--unsafe-fixes"):
        assert code == 0
        assert report["fixes"]["none_defaults"] == {
            "files": ["app/service.py"],
            "expressions": 1,
            "skipped": [],
        }
        assert (
            "options if options is not None else"
            in (root / "app/service.py").read_text()
        )
        assert main([*arguments, *flags]) == 0
        assert (
            json.loads(capsys.readouterr().out)["fixes"]["none_defaults"]["files"] == []
        )
    else:
        assert code == 1
        assert report["violations"][0]["code"] == "TYP003"
        assert (root / "app/service.py").read_text() == OPTIONAL_DEFAULT_SOURCE
        if "--fix" in flags:
            assert (
                "--unsafe-fixes"
                in report["fixes"]["none_defaults"]["skipped"][0]["reason"]
            )


def test_ordering_and_optional_defaults_are_rechecked_between_fixers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app], python_version: '3.12'}\nstyle:\n  overrides:\n    order:\n      match: app\n      ordering: {}\n",
            "app/__init__.py": "",
            "app/service.py": "def _default():\n    return 7\ndef create(options: int | None = None):\n    return options or _default()\n",
        },
    )
    assert (
        main(
            [
                "check",
                "--config",
                str(root / "smelt.yml"),
                "--select",
                "LAY004,TYP003",
                "--fail-on",
                "warning",
                "--fix",
                "--unsafe-fixes",
                "--format",
                "json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["fixes"]["ordering"]["files"] == ["app/service.py"]
    assert report["fixes"]["none_defaults"]["files"] == ["app/service.py"]
    rewritten = (root / "app/service.py").read_text()
    assert rewritten.startswith("def create")
    assert "options if options is not None else" in rewritten


def test_explain_points_a_retired_smt_code_to_its_family_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = _run(capsys, "explain", "SMT401")

    assert code != 0
    assert 'renamed to TST001 "tests/misplaced-test" in 0.14.0' in err
