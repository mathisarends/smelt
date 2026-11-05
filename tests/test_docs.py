import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from smelt.config.schema import SCHEMA_ID
from smelt.diagnostics.violation import DOCS_BASE_URL
from smelt.docs import (
    DOCS_DIR,
    SCHEMA_FILE,
    _generated_files,  # smelt: ignore[SMT420] -- Exercise the private analysis primitive and its safety boundaries directly.
    check_generated_files,
    write_generated_files,
)
from smelt.engine.check import rule_meta
from smelt.rules.registry import build_rule_set

ROOT = Path(__file__).resolve().parent.parent


class TestReleaseMetadata:
    def test_python_native_and_lockfile_versions_agree(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cargo = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
        versions = [project["project"]["version"], cargo["package"]["version"]]
        for filename in ("uv.lock", "Cargo.lock"):
            lock = tomllib.loads((ROOT / filename).read_text(encoding="utf-8"))
            [package] = [p for p in lock["package"] if p["name"] == "smelt-cli"]
            versions.append(package["version"])

        assert len(set(versions)) == 1, f"Release versions differ: {versions}"

    def test_current_release_has_dated_notes_and_upgrade_entry_points(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = project["project"]["version"]
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        assert re.search(
            rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
            changelog,
            re.MULTILINE,
        )
        assert (
            f"[Unreleased]: https://github.com/mathisarends/smelt/compare/v{version}...HEAD"
            in changelog
        )
        assert (
            f"[{version}]: https://github.com/mathisarends/smelt/compare/" in changelog
        )
        assert (ROOT / "docs/upgrading.md").is_file()
        assert "docs/upgrading.md" in (ROOT / "README.md").read_text(encoding="utf-8")
        integrations = (ROOT / "docs/integrations.md").read_text(encoding="utf-8")
        assert set(re.findall(r"smelt-cli==([\d.]+)", integrations)) == {version}
        assert set(re.findall(r"rev: v([\d.]+)", integrations)) == {version}


class TestGeneratedFiles:
    @pytest.mark.parametrize("name", sorted(_generated_files()))
    def test_committed_file_is_current(self, name: str) -> None:
        path = ROOT / name

        assert path.is_file(), f"run `uv run python scripts/generate.py` to add {name}"
        assert path.read_text(encoding="utf-8") == _generated_files()[name], (
            f"{name} is stale; run `uv run python scripts/generate.py`"
        )

    def test_writing_twice_changes_nothing(self, tmp_path: Path) -> None:
        write_generated_files(tmp_path)

        assert write_generated_files(tmp_path) == []

    def test_drift_check_detects_edits_missing_and_extra_files_without_writing(
        self, tmp_path: Path
    ) -> None:
        write_generated_files(tmp_path)
        assert check_generated_files(tmp_path) == []
        edited = tmp_path / "smelt/skills/smelt/references/docs/rules/SMT101.md"
        edited.write_text("incorrect rule advice", encoding="utf-8")
        missing = tmp_path / "docs/rules/SMT101.md"
        missing.unlink()
        extra = tmp_path / "smelt/skills/smelt/references/evaluation.json"
        extra.write_text("{}", encoding="utf-8")
        retired = tmp_path / "docs/rules/SMT000.md"
        retired.write_text("retired rule", encoding="utf-8")

        assert check_generated_files(tmp_path) == sorted(
            path.relative_to(tmp_path).as_posix()
            for path in (edited, missing, extra, retired)
        )
        assert edited.read_text(encoding="utf-8") == "incorrect rule advice"
        assert not missing.exists()
        assert extra.exists()
        assert retired.exists()

        write_generated_files(tmp_path)
        assert check_generated_files(tmp_path) == []
        assert not extra.exists()
        assert not retired.exists()

    def test_skill_reference_inventory_is_current(self) -> None:
        references = ROOT / "smelt/skills/smelt/references"
        found = {
            path.relative_to(ROOT).as_posix()
            for path in references.rglob("*")
            if path.is_file()
        }
        expected = {
            name
            for name in _generated_files()
            if name.startswith("smelt/skills/smelt/references/")
        }

        assert found == expected
        assert all(Path(name).suffix == ".md" for name in found)
        assert not (references / "docs/agent-workflow.md").exists()
        assert not (references / "docs/review.md").exists()

    def test_generator_cli_checks_without_writing_and_can_repair_drift(
        self, tmp_path: Path
    ) -> None:
        script = tmp_path / "scripts/generate.py"
        script.parent.mkdir()
        shutil.copyfile(ROOT / "scripts/generate.py", script)
        write_generated_files(tmp_path)
        command = [sys.executable, str(script)]
        clean = subprocess.run(  # noqa: S603 - trusted local generator in an isolated fixture
            [*command, "--check"], capture_output=True, check=False, timeout=30
        )
        assert clean.returncode == 0
        page = tmp_path / "smelt/skills/smelt/references/docs/rules/SMT101.md"
        original = page.read_bytes()
        page.write_text("stale advice", encoding="utf-8")

        drift = subprocess.run(  # noqa: S603 - trusted local generator in an isolated fixture
            [*command, "--check"], capture_output=True, check=False, timeout=30
        )
        assert drift.returncode == 1
        assert b"references/docs/rules/SMT101.md" in drift.stdout
        assert page.read_text(encoding="utf-8") == "stale advice"

        subprocess.run(command, capture_output=True, check=True, timeout=30)  # noqa: S603 - trusted local generator
        assert page.read_bytes() == original
        assert check_generated_files(tmp_path) == []

    def test_skill_links_to_omitted_maintainer_material_use_repository_urls(
        self,
    ) -> None:
        readme = (ROOT / "smelt/skills/smelt/references/README.md").read_text(
            encoding="utf-8"
        )

        assert "](CONTRIBUTING.md)" not in readme
        assert "](docs/agent-workflow.md#" not in readme
        assert (
            "https://github.com/mathisarends/smelt/blob/main/CONTRIBUTING.md" in readme
        )
        assert "](docs/configuration.md)" in readme

    def test_no_leftover_pages(self) -> None:
        written = {name for name in _generated_files() if name.startswith(DOCS_DIR)}
        found = {f"{DOCS_DIR}/{path.name}" for path in (ROOT / DOCS_DIR).glob("*.md")}

        assert found == written

    def test_regeneration_removes_obsolete_skill_references(
        self, tmp_path: Path
    ) -> None:
        obsolete = tmp_path / "smelt/skills/smelt/references/docs/rules/SMT000.md"
        obsolete.parent.mkdir(parents=True)
        obsolete.write_text("retired rule", encoding="utf-8")

        changed = write_generated_files(tmp_path)

        assert obsolete.relative_to(tmp_path).as_posix() in changed
        assert not obsolete.exists()
        assert (obsolete.parent / "SMT101.md").is_file()


class TestSchema:
    def test_id_matches_the_published_path(self) -> None:
        schema = json.loads((ROOT / SCHEMA_FILE).read_text(encoding="utf-8"))

        assert schema["$id"] == SCHEMA_ID
        assert SCHEMA_ID.endswith(f"/{SCHEMA_FILE}")
        assert schema["title"] == "smelt.yml"

    def test_third_party_accepts_the_short_form(self) -> None:
        schema = json.loads((ROOT / SCHEMA_FILE).read_text(encoding="utf-8"))

        policy = schema["$defs"]["ThirdPartyPolicy"]

        assert policy["anyOf"][0] == {"enum": ["allow", "deny"], "type": "string"}


class TestRulePages:
    def test_every_docs_url_points_at_a_page(self) -> None:
        rules = build_rule_set().rules
        assert rules
        for rule in rules:
            url = rule_meta(rule).docs_url
            assert url == f"{DOCS_BASE_URL}/{rule.code}.md"
            assert (ROOT / DOCS_DIR / f"{rule.code}.md").is_file()

    def test_page_carries_the_how_to_fix_section(self) -> None:
        page = (ROOT / DOCS_DIR / "SMT401.md").read_text(encoding="utf-8")

        assert "## How to fix\n\nMove or rename the file" in page
        assert page.startswith("# SMT401 test-location\n")

    def test_index_lists_every_rule(self) -> None:
        index = (ROOT / DOCS_DIR / "README.md").read_text(encoding="utf-8")

        rules = build_rule_set().rules
        assert rules
        for rule in rules:
            assert f"| [{rule.code}]({rule.code}.md) |" in index
