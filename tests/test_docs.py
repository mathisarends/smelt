import json
from pathlib import Path

import pytest

from smelt.config.schema import SCHEMA_ID
from smelt.diagnostics.violation import DOCS_BASE_URL
from smelt.docs import DOCS_DIR, SCHEMA_FILE, generated_files, write_generated_files
from smelt.engine.check import rule_meta
from smelt.rules.registry import build_rule_set

ROOT = Path(__file__).resolve().parent.parent


class TestGeneratedFiles:
    @pytest.mark.parametrize("name", sorted(generated_files()))
    def test_committed_file_is_current(self, name: str) -> None:
        path = ROOT / name

        assert path.is_file(), f"run `uv run python scripts/generate.py` to add {name}"
        assert path.read_text(encoding="utf-8") == generated_files()[name], (
            f"{name} is stale; run `uv run python scripts/generate.py`"
        )

    def test_writing_twice_changes_nothing(self, tmp_path: Path) -> None:
        write_generated_files(tmp_path)

        assert write_generated_files(tmp_path) == []

    def test_no_leftover_pages(self) -> None:
        written = {name for name in generated_files() if name.startswith(DOCS_DIR)}
        found = {f"{DOCS_DIR}/{path.name}" for path in (ROOT / DOCS_DIR).glob("*.md")}

        assert found == written


class TestSchema:
    def test_id_matches_the_published_path(self) -> None:
        schema = json.loads((ROOT / SCHEMA_FILE).read_text(encoding="utf-8"))

        assert schema["$id"] == SCHEMA_ID
        assert SCHEMA_ID.endswith(f"/{SCHEMA_FILE}")
        assert schema["title"] == "smelt.yaml"

    def test_third_party_accepts_the_short_form(self) -> None:
        schema = json.loads((ROOT / SCHEMA_FILE).read_text(encoding="utf-8"))

        policy = schema["$defs"]["ThirdPartyPolicy"]

        assert policy["anyOf"][0] == {"enum": ["allow", "deny"], "type": "string"}


class TestRulePages:
    def test_every_docs_url_points_at_a_page(self) -> None:
        for rule in build_rule_set(load_entry_points=False).rules:
            url = rule_meta(rule).docs_url
            assert url == f"{DOCS_BASE_URL}/{rule.code}.md"
            assert (ROOT / DOCS_DIR / f"{rule.code}.md").is_file()

    def test_page_carries_the_fix_hint(self) -> None:
        page = (ROOT / DOCS_DIR / "SMT401.md").read_text(encoding="utf-8")

        assert "- **Fixable:** `smelt fix SMT401`" in page
        assert page.startswith("# SMT401 test-location\n")

    def test_index_lists_every_rule(self) -> None:
        index = (ROOT / DOCS_DIR / "README.md").read_text(encoding="utf-8")

        for rule in build_rule_set(load_entry_points=False).rules:
            assert f"| [{rule.code}]({rule.code}.md) |" in index
