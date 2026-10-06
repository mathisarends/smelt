from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from smelt.cli import main
from smelt.config import load_config
from smelt.diagnostics.debt import Debt, DebtEntry, fingerprint
from tests.helpers import FIXTURES, LAYERED_CONFIG, check, write_project

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
    def test_public_hook_checks_incoming_edges_and_unrelated_python_files(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_project(
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
        assert "SMT101" in out
        assert "app/domain/model.py" in out
        assert err == ""

    def test_unclassified_library_warnings_are_grouped_only_in_text(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = clean_project / "smelt.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("[app]", "[app, lib]"),
            encoding="utf-8",
        )
        write_project(
            clean_project, {"lib/__init__.py": "", "lib/a.py": "", "lib/b.py": ""}
        )
        monkeypatch.chdir(clean_project)

        _, text, _ = _run(capsys, "check", "--select", "SMT305")
        _, output, _ = _run(capsys, "check", "--select", "SMT305", "--format", "json")

        assert text.count("SMT305 unclassified-module") == 1
        assert "2 modules have no architecture classification" in text
        assert "Give lib a layer" in text
        assert len(json.loads(output)["violations"]) == 2

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

        assert code == 2
        assert out == ""
        assert path in err
        assert "does not exist" in err or "no analyzed Python" in err

    @pytest.mark.parametrize("option", ["--select", "--ignore"])
    def test_rejects_unknown_selector(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        option: str,
    ) -> None:
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, "check", option, "SMT1,SMT999")

        assert code == 2
        assert out == ""
        assert option in err
        assert 'unknown rule prefix "SMT999"' in err
        assert "SMT903" not in err

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
        assert "SMT101" in scope["rules"]
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
        code, _, err = _run(
            capsys, *args, "--config", str(clean_project / "smelt.yaml")
        )

        assert (code, err) == (0, "")

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
    @pytest.mark.parametrize("format_name", ["yaml", "json"])
    def test_config_show_roundtrips_feature_exceptions(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        format_name: str,
    ) -> None:
        root = tmp_path / "gateway"
        shutil.copytree(GATEWAY, root)
        path = root / "smelt.yaml"
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
    def test_prune_upgrades_legacy_debt_without_accepting_new_violations(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = _violating_project()
        files["smelt.yaml"] += "debt: debt.json\n"
        root = write_project(tmp_path, files)
        [known] = check(root).unfiltered
        source = files["app/domain/model.py"]
        legacy = fingerprint(known, source, legacy=True)
        Debt([DebtEntry(legacy, known.code, known.path, known.message)]).write(
            root / "debt.json"
        )
        write_project(root, {"app/domain/new.py": source})
        monkeypatch.chdir(root)

        code, _, err = _run(capsys, "debt", "--prune")

        assert (code, err) == (0, "")
        debt = Debt.load(root / "debt.json")
        assert len(debt.entries) == 1
        assert debt.entries[0].fingerprint == fingerprint(known, source)
        assert debt.entries[0].fingerprint != legacy
        write_project(
            root, {"app/domain/model.py": source.rstrip() + " # changed comment\n"}
        )
        report = check(root).report
        assert report.in_debt == 1
        assert [v.path for v in report.violations] == ["app/domain/new.py"]

    @pytest.mark.parametrize(
        "content",
        ['{"version":1,"violations":[{}]}', '{"version":2,"violations":[]}', "{broken"],
    )
    def test_malformed_debt_is_a_config_error(
        self,
        capsys: pytest.CaptureFixture[str],
        clean_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        content: str,
    ) -> None:
        config = clean_project / "smelt.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + "debt: debt.json\n", encoding="utf-8"
        )
        (clean_project / "debt.json").write_text(content, encoding="utf-8")
        monkeypatch.chdir(clean_project)

        code, out, err = _run(capsys, "check", "--format", "json")

        assert code == 2
        assert out == ""
        assert "debt.json" in err
        assert "Traceback" not in err

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
