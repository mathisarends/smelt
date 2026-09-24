from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

from smelt.cli import main
from tests.helpers import FIXTURES, LAYERED_CONFIG, write_project

if TYPE_CHECKING:
    from pathlib import Path

GATEWAY = FIXTURES / "gateway"


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def clean_project(tmp_path: Path) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": LAYERED_CONFIG,
            "app/__init__.py": "",
            "app/domain/__init__.py": "",
            "app/application/__init__.py": "",
            "app/application/service.py": "from app import domain\n",
        },
    )


class TestCheckCommand:
    def test_exit_code_one_on_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(
            capsys, "--config", str(GATEWAY / "smelt.yaml"), "check", "--no-color"
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
        assert out == "✓ dependencies ✓ code ✓ structure ✓ tests · 4 modules\n"

    def test_json_document(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(
            capsys, "--config", str(GATEWAY / "smelt.yaml"), "check", "--format", "json"
        )

        document = json.loads(out)
        assert code == 1
        assert document["schema_version"] == 1
        assert document["status"] == "failed"
        assert document["summary"]["errors"] == 7
        assert document["violations"][0]["code"] == "SMT104"

    def test_select_narrows_rules(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = _run(
            capsys,
            "--config",
            str(GATEWAY / "smelt.yaml"),
            "check",
            "--format",
            "json",
            "--select",
            "SMT105",
        )

        assert [v["code"] for v in json.loads(out)["violations"]] == ["SMT105"]

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
            "smelt.yaml",
        )

        paths = {v["path"] for v in json.loads(out)["violations"]}
        assert "src/gateway/shared/clock.py" in paths
        assert len(paths) > 1

    def test_config_error_exits_with_two(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        (tmp_path / "smelt.yaml").write_text(
            "version: 1\nproject: {root_packages: [app]}\nbogus: 1\n"
        )

        code, _, err = _run(capsys, "--config", str(tmp_path / "smelt.yaml"), "check")

        assert code == 2
        assert 'bogus: unknown key "bogus"' in err

    def test_missing_config_exits_with_two(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)

        code, _, err = _run(capsys, "check")

        assert code == 2
        assert "no smelt.yaml found" in err

    def test_missing_root_package_exits_with_two(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        (tmp_path / "smelt.yaml").write_text(
            "version: 1\nproject: {root_packages: [nothere]}\n"
        )

        code, _, err = _run(capsys, "--config", str(tmp_path / "smelt.yaml"), "check")

        assert code == 2
        assert "root_packages were found" in err


class TestInfoCommands:
    def test_rules_json_lists_codes(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(capsys, "rules", "--format", "json")

        codes = [rule["code"] for rule in json.loads(out)]
        assert code == 0
        assert codes[:6] == ["SMT101", "SMT102", "SMT103", "SMT104", "SMT105", "SMT106"]

    def test_explain_by_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(capsys, "explain", "layer-boundary")

        assert code == 0
        assert out.startswith("SMT101 layer-boundary  [dependencies, default: error]")

    def test_explain_unknown_rule(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, _, err = _run(capsys, "explain", "SMT10")

        assert code == 2
        assert 'unknown rule "SMT10"' in err

    def test_config_schema_is_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(capsys, "config", "schema")

        assert code == 0
        assert json.loads(out)["title"] == "smelt.yaml"

    def test_config_show_fills_defaults(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, out, _ = _run(
            capsys,
            "--config",
            str(GATEWAY / "smelt.yaml"),
            "config",
            "show",
            "--format",
            "json",
        )

        data = json.loads(out)
        assert code == 0
        assert data["architecture"]["imports"] == {
            "type_checking": "include",
            "transitive": False,
            "cycles": ["features", "layers", "siblings"],
        }


class TestDiscoveryCommands:
    def test_context_for_feature(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(
            capsys, "--config", str(GATEWAY / "smelt.yaml"), "context", "voice"
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
            capsys, "--config", str(GATEWAY / "smelt.yaml"), "context", "voic"
        )

        assert code == 2
        assert 'did you mean "voice"?' in err


class TestInitCommand:
    def test_writes_inferred_config_and_reports_violations(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_project(
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
        assert (tmp_path / "smelt.yaml").is_file()
        assert "  layers: domain (domain), infrastructure (infra)\n" in out
        assert "The inferred config yields 1 error and 0 warnings." in out

    def test_refuses_to_overwrite(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, _, err = _run(capsys, "init")

        assert code == 2
        assert "smelt.yaml already exists (use --force to overwrite)" in err


class TestBaselineCommand:
    def test_writes_default_baseline_and_check_uses_it(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = write_project(tmp_path, _violating_project())
        monkeypatch.chdir(root)

        code, out, err = _run(capsys, "baseline")

        assert code == 0
        assert out == "Wrote .smelt/baseline.json (1 violation)\n"
        assert "add `baseline: .smelt/baseline.json` to smelt.yaml" in err
        with (root / "smelt.yaml").open("a", encoding="utf-8") as config:
            config.write("baseline: .smelt/baseline.json\n")
        assert _run(capsys, "check")[0] == 0

    def test_prune_removes_fixed_entries(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = _violating_project()
        files["smelt.yaml"] += "baseline: baseline.json\n"
        root = write_project(tmp_path, files)
        monkeypatch.chdir(root)
        _run(capsys, "baseline")
        (root / "app/domain/model.py").write_text("", encoding="utf-8")

        code, out, err = _run(capsys, "baseline", "--prune")

        assert code == 0
        assert out == "Removed 1 stale entry from baseline.json (0 remain)\n"
        assert err == ""
        data = json.loads((root / "baseline.json").read_text(encoding="utf-8"))
        assert data["violations"] == []


def _violating_project() -> dict[str, str]:
    return {
        "smelt.yaml": LAYERED_CONFIG,
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/model.py": "from app.application import service\n",
        "app/application/__init__.py": "",
        "app/application/service.py": "",
    }


class TestInspectCommand:
    def test_architecture_map(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(capsys, "--config", str(GATEWAY / "smelt.yaml"), "inspect")

        data = json.loads(out)
        assert code == 0
        assert data["schema_version"] == 1
        assert {feature["name"] for feature in data["features"]} >= {"voice"}
        assert data["violations"]["total"]["errors"] > 0
        assert all(edge["count"] > 0 for edge in data["edges"]["layers"])


def _python(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


class TestVerifyCommand:
    def _project(self, tmp_path: Path, steps: list[tuple[str, str]]) -> Path:
        lines = "".join(f"  - name: {name}\n    run: '{run}'\n" for name, run in steps)
        return write_project(
            tmp_path,
            {
                "smelt.yaml": LAYERED_CONFIG + "verify:\n" + lines,
                "app/__init__.py": "",
            },
        )

    def test_runs_steps_and_fails_on_error(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = self._project(
            tmp_path,
            [
                ("ok", _python("print(1)")),
                ("broken", _python("import sys; print(42); sys.exit(3)")),
                ("after", _python("print(2)")),
            ],
        )

        code, out, _ = _run(capsys, "--config", str(root / "smelt.yaml"), "verify")

        assert code == 1
        assert [line[:1] for line in out.splitlines()[:3]] == ["✓", "✗", " "]
        assert "    42" in out
        assert out.endswith("\n2/3 steps passed; failed: broken\n")

    def test_fail_fast_json(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        root = self._project(
            tmp_path,
            [("broken", _python("import sys; sys.exit(3)")), ("after", "echo x")],
        )

        code, out, _ = _run(
            capsys,
            "--config",
            str(root / "smelt.yaml"),
            "verify",
            "--format",
            "json",
            "--fail-fast",
        )

        data = json.loads(out)
        assert code == 1
        assert data["passed"] is False
        assert [(s["name"], s["status"], s["exit_code"]) for s in data["steps"]] == [
            ("broken", "failed", 3),
            ("after", "skipped", None),
        ]

    def test_requires_steps(
        self, capsys: pytest.CaptureFixture[str], clean_project: Path
    ) -> None:
        code, _, err = _run(
            capsys, "--config", str(clean_project / "smelt.yaml"), "verify"
        )

        assert code == 2
        assert "no verify steps configured" in err
