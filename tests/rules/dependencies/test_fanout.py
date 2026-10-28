from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """
version: 1
project: {root_packages: [app]}
structure:
  contracts:
    application-api:
      match: app.user.application
      bundle_imports_at: 3
"""
OPTIONS = CheckOptions(select=("SMT108",))


def _project(tmp_path: Path, caller: str, *, config: str = CONFIG) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "app/caller.py": caller,
            "app/user/application/__init__.py": "",
            "app/user/application/get_user.py": "GetUser = 1\nOther = 2\n",
            "app/user/application/update_user.py": "UpdateUser = 1\n",
            "app/user/application/delete_user.py": "DeleteUser = 1\n",
        },
    )


def test_three_distinct_modules_suggest_the_common_application_facade(
    tmp_path: Path,
) -> None:
    _project(
        tmp_path,
        "from app.user.application.get_user import GetUser\nfrom app.user.application.update_user import UpdateUser\nfrom app.user.application.delete_user import DeleteUser\n",
    )

    outcome = check(tmp_path, OPTIONS)
    found = outcome.report.violations

    assert len(found) == 1
    assert found[0].severity is Severity.HINT
    assert found[0].expected is not None
    assert found[0].expected["package"] == "app.user.application"
    assert found[0].expected["count"] == 3
    assert len(found[0].expected["modules"]) == 3
    assert not outcome.report.failed


def test_symbols_and_repeated_statements_do_not_inflate_module_count(
    tmp_path: Path,
) -> None:
    _project(
        tmp_path,
        "from app.user.application.get_user import GetUser\nfrom app.user.application.get_user import Other\nfrom app.user.application.get_user import GetUser as Again\nfrom app.user.application.update_user import UpdateUser\n",
    )

    assert violations(tmp_path, OPTIONS) == []


def test_grouped_module_imports_count_separate_dependencies(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from app.user.application import get_user, update_user, delete_user\n",
    )

    found = violations(tmp_path, OPTIONS)

    assert len(found) == 1
    assert found[0].expected is not None
    assert found[0].expected["count"] == 3


def test_no_hint_for_public_symbols_already_bundled_at_facade(tmp_path: Path) -> None:
    _project(
        tmp_path, "from app.user.application import GetUser, UpdateUser, DeleteUser\n"
    )
    write_project(
        tmp_path,
        {
            "app/user/application/__init__.py": "from .get_user import GetUser as GetUser\nfrom .update_user import UpdateUser as UpdateUser\nfrom .delete_user import DeleteUser as DeleteUser\n"
        },
    )

    assert violations(tmp_path, OPTIONS) == []


def test_internal_implementation_imports_do_not_count_as_outside_coupling(
    tmp_path: Path,
) -> None:
    _project(tmp_path, "")
    write_project(
        tmp_path,
        {
            "app/user/application/internal.py": "from .get_user import GetUser\nfrom .update_user import UpdateUser\nfrom .delete_user import DeleteUser\n"
        },
    )

    assert violations(tmp_path, OPTIONS) == []


def test_counts_are_per_file_not_across_all_callers(tmp_path: Path) -> None:
    _project(tmp_path, "from app.user.application.get_user import GetUser")
    write_project(
        tmp_path,
        {
            "app/second.py": "from app.user.application.update_user import UpdateUser\nfrom app.user.application.delete_user import DeleteUser"
        },
    )

    assert violations(tmp_path, OPTIONS) == []


def test_threshold_is_configurable_and_can_fail_with_explicit_hint_threshold(
    tmp_path: Path,
) -> None:
    _project(
        tmp_path,
        "from app.user.application.get_user import GetUser\nfrom app.user.application.update_user import UpdateUser",
        config=CONFIG.replace("at: 3", "at: 2"),
    )

    outcome = check(tmp_path, CheckOptions(select=("SMT108",), fail_on=Severity.HINT))

    assert outcome.report.failed


@pytest.mark.parametrize(("policy", "count"), [("include", 1), ("ignore", 0)])
def test_type_checking_imports_follow_existing_policy(
    tmp_path: Path, policy: str, count: int
) -> None:
    _project(
        tmp_path,
        "from app.user.application.get_user import GetUser\nfrom app.user.application.update_user import UpdateUser\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.user.application.delete_user import DeleteUser\n",
        config=CONFIG + f"\narchitecture:\n  imports: {{type_checking: {policy}}}",
    )

    assert len(violations(tmp_path, OPTIONS)) == count


def test_bundling_hints_are_disabled_without_explicit_threshold(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "from app.user.application import get_user, update_user, delete_user",
        config=CONFIG.replace("bundle_imports_at: 3", "required: [get_user]"),
    )

    assert violations(tmp_path, OPTIONS) == []
