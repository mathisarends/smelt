from __future__ import annotations

import json
import shutil
import subprocess
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
        assert out == "✓ dependencies ✓ structure ✓ tests · 4 modules\n"

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
        config = clean_project / "smelt.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + "rules: {SMT305: hint}\n",
            encoding="utf-8",
        )
        (clean_project / "app" / "misc.py").write_text("", encoding="utf-8")
        monkeypatch.chdir(clean_project)
        return clean_project

    @pytest.mark.usefixtures("repo")
    def test_changed_mode_shows_hints(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = _run(capsys, "check", "--changed", "--no-color")

        assert "app/misc.py  SMT305 unclassified-module  [hint]" in out

    @pytest.mark.usefixtures("repo")
    def test_full_check_folds_hints(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = _run(capsys, "check", "--no-color")

        assert "SMT305" not in out
        assert "1 hint (use --show-hints)" in out


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

    @pytest.mark.parametrize(
        ("raw", "suggestion"),
        [
            ("SMT999", ""),
            ("SMT102x", ""),
            ("SMT109", ""),
            ("SMT401", None),
            ("SMT411", ' (did you mean "SMT401"?)'),
            ("layer-boundry", ' (did you mean "layer-boundary"?)'),
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


class TestDebtCommand:
    def test_writes_default_debt_and_check_uses_it(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = write_project(tmp_path, _violating_project())
        monkeypatch.chdir(root)

        code, out, err = _run(capsys, "debt")

        assert (code, err) == (0, "")
        assert out == (
            "Wrote .smelt/debt.json (1 violation)\n"
            "Added `debt: .smelt/debt.json` to smelt.yaml\n"
        )
        assert _run(capsys, "check")[0] == 0

    def test_uncomments_the_debt_line_from_init(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = _violating_project()
        files["smelt.yaml"] += "# debt: .smelt/debt.json\n"
        root = write_project(tmp_path, files)
        monkeypatch.chdir(root)

        _run(capsys, "debt")

        config = (root / "smelt.yaml").read_text(encoding="utf-8")
        assert config.endswith("\ndebt: .smelt/debt.json\n")
        assert "# debt" not in config

    def test_prune_removes_fixed_entries(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = _violating_project()
        files["smelt.yaml"] += "debt: debt.json\n"
        root = write_project(tmp_path, files)
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
        "smelt.yaml": LAYERED_CONFIG,
        "app/__init__.py": "",
        "app/domain/__init__.py": "",
        "app/domain/model.py": "from app.application import service\n",
        "app/application/__init__.py": "",
        "app/application/service.py": "",
    }


def _python(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'
