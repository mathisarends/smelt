from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config import load_config
from smelt.engine.coverage import policy_hash
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project:
  root_packages: [app]
  source_roots: [app/src]
architecture:
  features: {root: app.features}
  composition_root: [app.main]
  wiring: [app.features.*.di]
  layers:
    domain: {path: domain, third_party: deny}
    application: {path: application, may_depend_on: [domain]}
  cross_feature:
    allow:
      - "application -> application"
      - {from: billing.application, to: users.domain}
  imports: {transitive: true, cycles: [features]}
"""


def _project(root: Path) -> Path:
    return write_project(
        root,
        {
            "smelt.yaml": CONFIG,
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["app", "libs/*"]\n',
            "app/src/app/__init__.py": "",
            "app/src/app/main.py": "",
            "app/src/app/scripts.py": "",
            "app/src/app/features/__init__.py": "",
            "app/src/app/features/billing/__init__.py": "",
            "app/src/app/features/billing/di.py": "",
            "app/src/app/features/billing/domain/__init__.py": "",
            "app/src/app/features/billing/application/__init__.py": "",
            "app/src/app/features/users/__init__.py": "",
            "app/src/app/features/users/domain/__init__.py": "",
            "libs/tokens/src/tokens/__init__.py": "",
            "libs/docs/README.md": "",
        },
    )


class TestEffectiveCoverage:
    def test_names_what_was_not_analyzed_and_what_is_exempt(
        self, tmp_path: Path
    ) -> None:
        coverage = check(_project(tmp_path)).report.coverage

        assert coverage is not None
        assert coverage["omitted_workspace_members"] == [
            {
                "path": "libs/docs",
                "reason": "no Python package in src/ or the member directory",
            },
            {"path": "libs/tokens", "reason": "not in project.root_packages: tokens"},
        ]
        assert coverage["exempt"] == {
            "composition_root": ["app.main"],
            "wiring": ["app.features.*.di"],
        }
        assert coverage["modules"] == {
            "total": 10,
            "feature": 6,
            "shared": 0,
            "composition_root": 1,
            "unclassified": 3,
            "wiring": 1,
            "central": 0,
        }
        assert coverage["third_party"]["domain"] == {
            "default": "deny",
            "allow": [],
            "deny": [],
        }
        assert coverage["imports"] == {
            "type_checking": "include",
            "transitive": True,
            "cycles": ["features"],
        }
        assert coverage["cross_feature"]["allow"] == [
            {
                "source_feature": None,
                "source_layer": "application",
                "target_feature": None,
                "target_layer": "application",
            },
            {
                "source_feature": "billing",
                "source_layer": "application",
                "target_feature": "users",
                "target_layer": "domain",
            },
        ]

    def test_policy_hash_changes_with_the_policy_only(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        before = policy_hash(load_config(root / "smelt.yaml").config)
        config = root / "smelt.yaml"
        config.write_text(
            "# a comment does not change the policy\n"
            + config.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        same = policy_hash(load_config(config).config)
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "transitive: true", "transitive: false"
            ),
            encoding="utf-8",
        )

        assert same == before
        assert policy_hash(load_config(config).config) != before
