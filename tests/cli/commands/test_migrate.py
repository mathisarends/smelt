from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from smelt.cli import main
from smelt.config import load_config
from tests.helpers import MEMBER_BOUNDARIES, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_migrate_previews_and_writes_a_mixed_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "smelt.yml"
    before = """# policy note
version: 1
project: {root_packages: [app]}
architecture:
  imports: {type_checking: ignore}
structure:
  contracts:
    api:
      match: app
      public: []  # closed API
      export_at: 3
      max_positional_parameters: 3
rules:
  no-base-class-prefix: warning  # legacy prefix policy
  no-base-class-suffix: error  # keep the suffix rationale
  information-hiding: warning
ignore:
  - rule: prefer-package-import
    paths: [app/tool.py]
    reason: "external tool"
"""
    path.write_text(before, encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").touch()
    args = ["--config", str(path), "config", "migrate"]
    assert main(args) == 0
    preview = capsys.readouterr().out
    assert "+++ smelt.yml" in preview
    assert "Merged no-base-class-suffix" in preview
    assert (
        "Moved style.overrides.api.max_positional_parameters to "
        "style.overrides.api.rules.complexity/excess-positional-parameters.max."
        in preview
    )
    assert "Updated version 1 to version 2." in preview
    assert path.read_text(encoding="utf-8") == before

    assert main([*args, "--write"]) == 0
    after = path.read_text(encoding="utf-8")
    assert "# policy note" in after
    assert "# legacy prefix policy" in after
    assert "# keep the suffix rationale" in after
    assert "# closed API" in after
    data = yaml.safe_load(after)
    assert data["architecture"]["packages"]["api"] == {
        "match": "app",
        "public": [],
        "min_importers_to_export": 3,
    }
    # The option enabled its rule in the override; its entry now carries the limit.
    assert data["version"] == 2
    assert data["style"]["overrides"]["api"] == {
        "match": "app",
        "rules": {
            "complexity/excess-positional-parameters": {"severity": "warning", "max": 3}
        },
    }
    assert data["architecture"]["import_graph"] == {"type_checking": "ignore"}
    assert data["rules"]["modeling/forbidden-base-class-affix"] == "error"
    assert list(data["rules"].values()).count("warning") == 5
    assert data["ignore"][0]["rule"] == "imports/prefer-package-import"
    assert main([*args, "--write"]) == 0
    assert path.read_text(encoding="utf-8") == after


def test_unresolved_key_keeps_original_and_reports_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "smelt.yml"
    before = "version: 1\nstructure: {unknown_option: 2}\n"
    path.write_text(before, encoding="utf-8")
    assert main(["--config", str(path), "config", "migrate", "--write"]) == 2
    assert "style.unknown_option" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == before


def test_migrate_keeps_a_comment_only_base(tmp_path: Path) -> None:
    comment = "# This base has no shared settings yet.\n"
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nextends: base.yml\nproject: {root_packages: [app]}\n",
            "base.yml": comment,
            "app/__init__.py": "",
        },
    )
    path = tmp_path / "smelt.yml"

    assert main(["--config", str(path), "config", "migrate", "--write"]) == 0

    assert load_config(path).config.version == 2
    assert (tmp_path / "base.yml").read_text(encoding="utf-8") == comment


def test_migrate_keeps_a_rule_active_outside_its_local_exception(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": """
                version: 1
                project: {root_packages: [app]}
                style:
                  module_docstrings: require
                  overrides:
                    domain:
                      match: app.domain
                      rules: {layout/module-docstring-convention: off}
                """,
            "app/__init__.py": '"""Application package."""\n',
            "app/domain/__init__.py": "",
            "app/api.py": "",
        },
    )
    path = tmp_path / "smelt.yml"

    assert main(["--config", str(path), "config", "migrate", "--write"]) == 0

    assert [(finding.code, finding.path) for finding in violations(tmp_path)] == [
        ("LAY002", "app/api.py")
    ]


def test_migrate_preserves_a_family_severity_cap(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": """
                version: 1
                project: {root_packages: [app]}
                architecture:
                  layers:
                    domain: {path: domain}
                    api: {path: api, may_depend_on: [domain]}
                rules: {architecture: hint}
                """,
            "app/__init__.py": "",
            "app/domain/__init__.py": "from app.api import handle\n",
            "app/api/__init__.py": "def handle(): pass\n",
        },
    )
    path = tmp_path / "smelt.yml"

    assert main(["--config", str(path), "config", "migrate", "--write"]) == 0

    assert [
        (finding.code, finding.severity.value) for finding in violations(tmp_path)
    ] == [("ARC001", "hint")]


def test_migrate_preserves_inherited_severity_for_a_scoped_limit(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "base.yml": "rules: {complexity/excess-positional-parameters: error}\n",
            "smelt.yml": """
                version: 1
                extends: base.yml
                project: {root_packages: [app]}
                style:
                  overrides:
                    domain:
                      match: app.domain
                      max_positional_parameters: 2
                """,
            "app/__init__.py": "",
            "app/domain/__init__.py": "def build(a, b, c): pass\n",
            "app/api.py": "def handle(a, b, c): pass\n",
        },
    )
    path = tmp_path / "smelt.yml"

    assert main(["--config", str(path), "config", "migrate", "--write"]) == 0

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["style"]["overrides"]["domain"]["rules"] == {
        "complexity/excess-positional-parameters": {"max": 2}
    }
    assert [
        (finding.code, finding.path, finding.severity.value)
        for finding in violations(tmp_path)
    ] == [("CPX003", "app/domain/__init__.py", "error")]


def test_migrate_converts_the_bases_and_members_of_a_workspace_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {
        **MEMBER_BOUNDARIES,
        "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"].replace("version: 2", "version: 1")
        + "extends: base.yml\n",
        "base.yml": "style: {max_module_imports: 12}\n",
        "services/api/smelt.yml": "version: 1\nstyle: {max_positional_parameters: 2}\n",
    }
    write_project(tmp_path, files)
    args = ["--config", str(tmp_path / "smelt.yml"), "config", "migrate"]

    assert main([*args, "--write"]) == 0

    output = capsys.readouterr().out
    assert "base.yml: Moved style.max_module_imports" in output
    assert "services/api/smelt.yml: Updated version 1 to version 2." in output
    assert yaml.safe_load((tmp_path / "base.yml").read_text(encoding="utf-8")) == {
        "rules": {"imports/excess-module-imports": {"severity": "hint", "max": 12}}
    }
    assert yaml.safe_load(
        (tmp_path / "services/api/smelt.yml").read_text(encoding="utf-8")
    ) == {
        "version": 2,
        "rules": {
            "complexity/excess-positional-parameters": {"severity": "warning", "max": 2}
        },
    }
    assert load_config(tmp_path / "smelt.yml").config.version == 2
