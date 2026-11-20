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

    assert yaml.safe_load(migrated.text) == {"version": 2}
    assert migrated.notes == (
        "Removed project.coverage; configured coverage is the default.",
        "Updated version 1 to version 2.",
    )


def test_strict_coverage_keeps_an_existing_guard_setting() -> None:
    migrated = migrate_policy(
        "version: 1\nproject: {coverage: strict}\nrules: {ARC013: error}\n"
    )

    assert yaml.safe_load(migrated.text)["rules"] == {"ARC013": "error"}


def test_enable_rules_adds_missing_entries_and_keeps_comments() -> None:
    text = "version: 1  # policy\nrules:\n  architecture/layer-boundary: hint\n"

    migrated = enable_rules(
        text,
        [
            (("rules",), "architecture/layer-boundary", "error"),
            (("style", "overrides", "api", "rules"), "CPX003", "warning"),
        ],
    )

    assert "# policy" in migrated.text
    data = yaml.safe_load(migrated.text)
    assert data["rules"] == {"architecture/layer-boundary": "hint"}
    assert data["style"]["overrides"]["api"]["rules"] == {"CPX003": "warning"}
    assert migrated.notes == (
        "Enabled CPX003 in style.overrides.api.rules; its policy enabled it implicitly before.",
    )


def test_enable_rules_without_entries_keeps_the_text() -> None:
    text = "version: 1\n"

    assert enable_rules(text, []).text == text


def test_enable_rules_appends_before_the_blank_line_closing_the_map() -> None:
    text = "rules:\n  ARC004: error\n\nproject: {}\n"

    migrated = enable_rules(text, [(("rules",), "ARC001", "error")])

    assert migrated.text == "rules:\n  ARC004: error\n  ARC001: error\n\nproject: {}\n"


def test_enable_rules_keeps_the_inline_comment_of_the_last_entry() -> None:
    text = "rules:\n  ARC004: error  # keep\n\nproject: {}\n"

    migrated = enable_rules(text, [(("rules",), "ARC001", "error")])

    assert migrated.text == (
        "rules:\n  ARC004: error  # keep\n  ARC001: error\n\nproject: {}\n"
    )


def test_limits_become_the_max_of_their_rule_entry_in_the_same_scope() -> None:
    migrated = migrate_policy(
        """\
version: 1
style:
  max_positional_parameters: 3
  overrides:
    api:
      match: app.api
      max_positional_parameters: {value: 2, reason: Handlers take named input.}
    jobs:
      match: app.jobs
      max_comprehension_loops: 2
tests:
  max_patches_per_test: 0
rules:
  complexity/excess-positional-parameters: error
"""
    )

    data = yaml.safe_load(migrated.text)
    assert data["version"] == 2
    assert "style" not in data or "max_positional_parameters" not in data["style"]
    assert "tests" not in data
    assert data["rules"] == {
        "complexity/excess-positional-parameters": {"severity": "error", "max": 3},
        "tests/excess-patches": {"severity": "error", "max": 0},
    }
    overrides = data["style"]["overrides"]
    # The root enables CPX003, so the override refines only the limit.
    assert overrides["api"]["rules"] == {
        "complexity/excess-positional-parameters": {
            "max": 2,
            "reason": "Handlers take named input.",
        }
    }
    # The override option enabled CPX006 there, so its entry keeps the default severity.
    assert overrides["jobs"]["rules"] == {
        "complexity/excess-comprehension-loops": {"severity": "hint", "max": 2}
    }
    assert migrated.notes == (
        "Moved style.max_positional_parameters to "
        "rules.complexity/excess-positional-parameters.max.",
        "Moved tests.max_patches_per_test to rules.tests/excess-patches.max.",
        "Moved style.overrides.api.max_positional_parameters to "
        "style.overrides.api.rules.complexity/excess-positional-parameters.max.",
        "Moved style.overrides.jobs.max_comprehension_loops to "
        "style.overrides.jobs.rules.complexity/excess-comprehension-loops.max.",
        "Updated version 1 to version 2.",
    )


def test_pre_family_codes_become_rule_names() -> None:
    migrated = migrate_policy(
        "version: 2\nrules: {SMT345: error}\nignore:\n  - {rule: SMT101, paths: [a.py]}\n"
    )

    data = yaml.safe_load(migrated.text)
    assert data["rules"] == {"complexity/excess-positional-parameters": "error"}
    assert data["ignore"] == [{"rule": "ARC001", "paths": ["a.py"]}]
