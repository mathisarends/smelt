from __future__ import annotations

import yaml

from smelt.engine.migration import enable_rules, migrate_policy


def test_strict_coverage_becomes_the_guard_rule() -> None:
    migrated = migrate_policy(
        "version: 1\nproject:\n  root_packages: [app]\n  coverage: strict\n"
    )

    data = yaml.safe_load(migrated.text)
    assert data["project"] == {"root_packages": ["app"]}
    assert data["rules"] == {"architecture/uncovered-source": "error"}


def test_configured_coverage_is_dropped_with_an_empty_project() -> None:
    migrated = migrate_policy("version: 1\nproject:\n  coverage: configured\n")

    assert yaml.safe_load(migrated.text) == {"version": 1}
    assert migrated.notes == (
        "Removed project.coverage; configured coverage is the default.",
    )


def test_strict_coverage_keeps_an_existing_guard_setting() -> None:
    migrated = migrate_policy(
        "version: 1\nproject: {coverage: strict}\nrules: {SMT306: error}\n"
    )

    assert yaml.safe_load(migrated.text)["rules"] == {"SMT306": "error"}


def test_enable_rules_adds_missing_entries_and_keeps_comments() -> None:
    text = "version: 1  # policy\nrules:\n  architecture/layer-boundary: hint\n"

    migrated = enable_rules(
        text,
        [
            (("rules",), "architecture/layer-boundary", "error"),
            (("style", "overrides", "api", "rules"), "SMT345", "warning"),
        ],
    )

    assert "# policy" in migrated.text
    data = yaml.safe_load(migrated.text)
    assert data["rules"] == {"architecture/layer-boundary": "hint"}
    assert data["style"]["overrides"]["api"]["rules"] == {"SMT345": "warning"}
    assert migrated.notes == (
        "Enabled SMT345 in style.overrides.api.rules; its policy enabled it implicitly before.",
    )


def test_enable_rules_without_entries_keeps_the_text() -> None:
    text = "version: 1\n"

    assert enable_rules(text, []).text == text


def test_enable_rules_appends_before_the_blank_line_closing_the_map() -> None:
    text = "rules:\n  SMT104: error\n\nproject: {}\n"

    migrated = enable_rules(text, [(("rules",), "SMT101", "error")])

    assert migrated.text == "rules:\n  SMT104: error\n  SMT101: error\n\nproject: {}\n"
