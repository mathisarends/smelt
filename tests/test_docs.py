import json
import re
import tomllib
from pathlib import Path

import pytest

from smelt.config.schema import SCHEMA_ID
from smelt.diagnostics.violation import DOCS_BASE_URL
from smelt.docs import (
    DOCS_DIR,
    SCHEMA_FILE,
    _generated_files,  # smelt: ignore[SMT420] -- Exercise the private analysis primitive and its safety boundaries directly.
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
