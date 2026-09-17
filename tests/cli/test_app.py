import json
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
        assert out == "✓ dependencies ✓ code ✓ structure · 4 modules\n"

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

    def test_where_port(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = _run(
            capsys,
            "--config",
            str(GATEWAY / "smelt.yaml"),
            "where",
            "port",
            "--feature",
            "voice",
        )

        assert code == 0
        assert out == "src/gateway/features/voice/application/ports.py\n"

    def test_where_requires_feature(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, _, err = _run(
            capsys, "--config", str(GATEWAY / "smelt.yaml"), "where", "port"
        )

        assert code == 2
        assert "--feature is required (features: billing, voice)" in err

    def test_where_new_feature_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, err = _run(
            capsys,
            "--config",
            str(GATEWAY / "smelt.yaml"),
            "where",
            "domain",
            "--feature",
            "payments",
        )

        assert code == 0
        assert out == "src/gateway/features/payments/domain/\n"
        assert 'feature "payments" does not exist yet' in err
