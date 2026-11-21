from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config.errors import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

_CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
_ENABLED = _CONFIG + "rules:\n  imports/prefer-package-import: error\n"
_IMPORT = "from app.jobs.worker import start, stop\n"
_CALLERS = ("app/api.py", "app/cli.py", "app/web.py")


def _project(
    tmp_path: Path,
    *,
    exports: str = "",
    config: str = _ENABLED,
    statement: str = _IMPORT,
    callers: tuple[str, ...] = _CALLERS,
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/jobs/__init__.py": exports,
            "app/jobs/worker.py": "def start(): ...\ndef stop(): ...\n",
            **dict.fromkeys(callers, statement),
        },
    )


def _findings(root: Path, options: CheckOptions | None = None) -> list[str]:
    report = check(root, options or CheckOptions()).report
    return [f"{v.code}:{v.path}" for v in report.violations]


@pytest.mark.parametrize(
    ("exports", "statement", "expected"),
    [
        ("", _IMPORT, ["MET005:app/jobs/__init__.py"]),
        ("from .worker import start\n", _IMPORT, ["MET005:app/jobs/__init__.py"]),
        (
            "from .worker import start, stop\n",
            _IMPORT,
            [f"IMP004:{caller}" for caller in _CALLERS],
        ),
        ("from .worker import start, stop\n", "from app.jobs import start, stop\n", []),
    ],
)
def test_facade_coverage_follows_the_direct_exports(
    tmp_path: Path, exports: str, statement: str, expected: list[str]
) -> None:
    root = _project(tmp_path, exports=exports, statement=statement)

    assert _findings(root) == expected


def test_the_hint_counts_callers_and_names_what_the_facade_lacks(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, exports="from .worker import start\n")

    [finding] = check(root).report.violations

    assert (finding.line, finding.severity) == (1, Severity.HINT)
    assert finding.expected is not None
    assert finding.expected["callers"] == 3
    assert finding.expected["implementation_modules"] == 1
    assert finding.expected["sample_names"] == ["app.jobs.worker.stop"]


def test_callers_at_the_limit_stay_silent(tmp_path: Path) -> None:
    assert _findings(_project(tmp_path, callers=_CALLERS[:2])) == []


def test_max_sets_the_tolerated_number_of_callers(tmp_path: Path) -> None:
    config = _ENABLED + "  meta/uncovered-package-facade: {severity: warning, max: 3}\n"
    root = _project(tmp_path, config=config)
    assert _findings(root) == []

    write_project(root, {"app/cron.py": _IMPORT})
    [finding] = check(root).report.violations
    assert (finding.code, finding.severity) == ("MET005", Severity.WARNING)


def test_max_belongs_to_the_root_rules(tmp_path: Path) -> None:
    config = (
        _ENABLED + "style:\n  overrides:\n    jobs:\n      match: app.jobs\n"
        "      rules: {meta/uncovered-package-facade: {max: 5}}\n"
    )
    with pytest.raises(ConfigError, match="applies to the whole project"):
        check(_project(tmp_path, config=config))


@pytest.mark.parametrize(
    "callers",
    [
        # Package-internal imports never route through the package's own facade.
        ("app/jobs/a.py", "app/jobs/b.py", "app/jobs/c.py"),
        # Tests and initializers are no production callers.
        ("tests/test_a.py", "tests/test_b.py", "app/other/__init__.py"),
    ],
)
def test_only_outside_production_modules_count(
    tmp_path: Path, callers: tuple[str, ...]
) -> None:
    assert _findings(_project(tmp_path, callers=callers)) == []


@pytest.mark.parametrize(
    "statement",
    ["from app.jobs.worker import _start\n", "from app.jobs.worker import *\n"],
)
def test_private_and_star_imports_do_not_count(tmp_path: Path, statement: str) -> None:
    assert _findings(_project(tmp_path, statement=statement)) == []


def test_cli_selection_alone_is_no_policy(tmp_path: Path) -> None:
    root = _project(tmp_path, config=_CONFIG)

    assert _findings(root, CheckOptions(select=("IMP004", "MET005"))) == []


def test_a_package_setting_can_turn_the_rule_off_for_its_callers(
    tmp_path: Path,
) -> None:
    config = (
        _ENABLED + "style:\n  overrides:\n    front:\n      match: app.front\n"
        "      rules: {imports/prefer-package-import: off}\n"
    )
    root = _project(tmp_path, config=config, callers=_CALLERS[:2])
    write_project(root, {"app/front/__init__.py": "", "app/front/views.py": _IMPORT})

    assert _findings(root) == []


@pytest.mark.parametrize(
    ("rule", "module"),
    [
        ("imports/prefer-package-import", "app.api"),
        ("meta/uncovered-package-facade", "app.jobs"),
    ],
)
def test_ignore_entries_exclude_callers_or_the_package(
    tmp_path: Path, rule: str, module: str
) -> None:
    config = (
        _ENABLED + f"ignore:\n  - {{rule: {rule}, modules: [{module}],"
        " reason: deliberately facade-less}\n"
    )
    assert _findings(_project(tmp_path, config=config)) == []


def test_an_active_export_threshold_owns_the_package(tmp_path: Path) -> None:
    config = (
        _ENABLED + "  architecture/missing-facade-export: warning\n"
        "architecture:\n  packages:\n"
        "    jobs: {match: app.jobs, min_importers_to_export: 99}\n"
    )
    assert _findings(_project(tmp_path, config=config)) == []


@pytest.mark.parametrize(
    "options",
    [
        CheckOptions(paths=("app",)),
        CheckOptions(staged=True),
    ],
)
def test_narrow_checks_skip_the_hint(tmp_path: Path, options: CheckOptions) -> None:
    assert _findings(_project(tmp_path), options) == []


def test_cached_results_follow_a_new_reexport(tmp_path: Path) -> None:
    root = _project(tmp_path, exports="from .worker import start\n")
    assert _findings(root) == ["MET005:app/jobs/__init__.py"]

    write_project(root, {"app/jobs/__init__.py": "from .worker import start, stop\n"})

    warm = check(root).report.violations
    fresh = check(root, CheckOptions(use_cache=False)).report.violations
    assert warm == fresh
    assert {v.code for v in warm} == {"IMP004"}
