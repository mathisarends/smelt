from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.config import load_config
from smelt.engine.check import CheckOptions, CheckOutcome, run_check

if TYPE_CHECKING:
    from smelt.diagnostics.violation import Violation

FIXTURES = Path(__file__).parent / "fixtures"


def write_project(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return root


def check(root: Path, options: CheckOptions | None = None) -> CheckOutcome:
    loaded = load_config(root / "smelt.yml")
    return run_check(loaded, options or CheckOptions())


def violations(root: Path, options: CheckOptions | None = None) -> list[Violation]:
    return check(root, options).report.violations


def codes_at(found: list[Violation]) -> list[tuple[str, str | None, int | None]]:
    return [(v.code, v.path, v.line) for v in found]


LAYERED_CONFIG = """
version: 1
project:
  root_packages: [app]
architecture:
  layers:
    domain:
      path: domain
    application:
      path: application
      may_depend_on: [domain]
    infrastructure:
      path: infra
      may_depend_on: [domain, application]
"""

MEMBER_ARCHITECTURES = {
    "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*", "packages/*"]\n',
    "smelt.yml": "version: 1\nproject: {workspace: uv, source_roots: [src], coverage: strict}\n",
    "services/api/smelt.yml": """
        architecture:
          features: {root: api.features}
          layers:
            domain: {path: domain}
            application: {path: application, may_depend_on: [domain]}
        """,
    "services/api/src/api/__init__.py": "",
    "services/api/src/api/features/__init__.py": "",
    "services/api/src/api/features/orders/__init__.py": "",
    "services/api/src/api/features/orders/domain/__init__.py": "",
    "services/api/src/api/features/orders/domain/order.py": "from api.features.users.domain import model\n",
    "services/api/src/api/features/users/__init__.py": "",
    "services/api/src/api/features/users/application/__init__.py": "x = 1\n",
    "services/api/src/api/features/users/application/service.py": "from core.domain import thing\n",
    "services/api/src/api/features/users/domain/__init__.py": "",
    "services/api/src/api/features/users/domain/model.py": "from api.features.users.application import x\n",
    "packages/core/smelt.yml": """
        architecture:
          layers:
            domain: {path: domain, third_party: deny}
            infra: {path: infra, may_depend_on: [domain]}
        """,
    "packages/core/src/core/__init__.py": "",
    "packages/core/src/core/domain/__init__.py": "",
    "packages/core/src/core/domain/thing.py": "import requests\nfrom core.infra import db\n",
    "packages/core/src/core/infra/__init__.py": "",
    "packages/core/src/core/infra/db.py": "db = 1\n",
}
"""A uv workspace whose two members each declare their own architecture."""

MEMBER_BOUNDARIES = {
    "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*", "packages/*"]\n',
    "smelt.yml": """
        version: 1
        project: {workspace: uv, source_roots: [src]}
        architecture:
          members:
            services/*: {may_depend_on: [packages/*]}
            packages/*: {may_depend_on: []}
        """,
    "services/api/pyproject.toml": '[project]\nname = "web-api"\ndependencies = ["core-lib"]\n[tool.uv.sources]\ncore-lib = {workspace = true}\n',
    "services/api/src/api/__init__.py": "",
    "services/api/src/api/http.py": "from core.model import Order\n",
    "packages/core/pyproject.toml": '[project]\nname = "core-lib"\ndependencies = []\n',
    "packages/core/src/core/__init__.py": "",
    "packages/core/src/core/model.py": "class Order: pass\n",
}
"""A uv workspace with declared dependencies and root member boundaries."""
