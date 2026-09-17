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
    loaded = load_config(root / "smelt.yaml")
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
