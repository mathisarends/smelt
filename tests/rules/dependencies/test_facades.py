from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config.errors import ConfigError
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


CONFIG = """
version: 1
project: {root_packages: [app], test_roots: [tests]}
structure:
  contracts:
    browser-api: {match: app.browser, export_at: 3}
"""
OPTIONS = CheckOptions(select=("SMT109",))
CLIENT = "from app.browser.base import Browser, BrowserEvents\n"


def _project(
    tmp_path: Path,
    *,
    config: str = CONFIG,
    exports: str = "from .base import Browser\n",
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": config,
            "app/__init__.py": "",
            "app/browser/__init__.py": exports,
            "app/browser/base.py": "class Browser: pass\nclass BrowserEvents: pass\n",
            "app/first.py": CLIENT,
            "app/second.py": CLIENT,
            "app/third.py": CLIENT,
        },
    )


def test_reports_one_missing_name_at_its_definition_with_mixed_imports(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    before = (root / "app/browser/__init__.py").read_bytes()

    [finding] = violations(root)  # export_at activates the rule without a second switch

    assert finding.code == "SMT109"
    assert finding.severity is Severity.HINT
    assert (finding.path, finding.line, finding.column) == ("app/browser/base.py", 2, 1)
    assert finding.source_module == "app.browser.base"
    assert finding.target_module == "app.browser"
    assert finding.expected is not None
    assert finding.expected["name"] == "BrowserEvents"
    assert finding.expected["source_importers"] == [
        "app.first",
        "app.second",
        "app.third",
    ]
    assert finding.expected["count"] == 3
    assert finding.expected["cycle_check"] == "no-cycle-found"
    assert finding.expected["automatic_rewrite"] is False
    assert (root / "app/browser/__init__.py").read_bytes() == before


def test_repeated_aliases_and_local_or_relative_imports_count_distinct_modules(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    write_project(
        root,
        {
            "app/first.py": "from .browser.base import BrowserEvents as Events\n" * 3,
            "app/second.py": "def run():\n    from app.browser.base import BrowserEvents\n",
            "app/third.py": "",
            "app/browser/internal.py": "from .base import BrowserEvents\n",
        },
    )
    assert violations(root, OPTIONS) == []
    write_project(root, {"app/third.py": CLIENT})
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["count"] == 3


@pytest.mark.parametrize("count_tests", [False, True])
def test_tests_only_drive_the_threshold_when_explicitly_requested(
    tmp_path: Path, count_tests: bool
) -> None:
    root = _project(
        tmp_path,
        config=CONFIG.replace(
            "export_at: 3", f"export_at: 3, count_tests: {str(count_tests).lower()}"
        ),
    )
    write_project(
        root,
        {
            "app/third.py": "",
            "tests/test_browser.py": CLIENT,
            "tests/test_typing.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.browser.base import BrowserEvents\n",
        },
    )
    found = violations(root, OPTIONS)
    assert len(found) == int(count_tests)
    if count_tests:
        assert found[0].expected is not None
        assert found[0].expected["test_importers"] == ["tests.test_browser"]
        assert found[0].expected["source_importers"] == ["app.first", "app.second"]


@pytest.mark.parametrize("policy", ["include", "ignore"])
def test_type_checking_only_imports_never_drive_api_expansion(
    tmp_path: Path, policy: str
) -> None:
    root = _project(
        tmp_path,
        config=CONFIG + f"architecture: {{imports: {{type_checking: {policy}}}}}\n",
    )
    write_project(
        root,
        {
            "app/third.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.browser.base import BrowserEvents\n"
        },
    )
    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize(
    "client",
    [
        "from app.browser.base import _Events\n",
        "from app.browser.base import *\n",
        "import app.browser.base\n",
        "from app.browser import BrowserEvents\n",
        "from app.browser import base\n",
    ],
)
def test_private_star_module_and_facade_imports_do_not_create_gap_findings(
    tmp_path: Path, client: str
) -> None:
    root = _project(tmp_path)
    write_project(
        root, {f"app/{name}.py": client for name in ("first", "second", "third")}
    )
    assert violations(root, OPTIONS) == []


@pytest.mark.parametrize(
    "exports",
    [
        "from .base import BrowserEvents\n",
        "from .api import BrowserEvents\n__all__ = ['Browser', 'BrowserEvents']\n",
        "from .base import BrowserEvents\n__all__ = build_exports()\n",
        "def __getattr__(name): return object()\n",
        "from .base import *\n",
        "if enabled:\n    from .base import BrowserEvents\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .base import BrowserEvents\n",
        "BrowserEvents = object()\n",
        "globals().update({'BrowserEvents': object()})\n",
        "exec('from .base import BrowserEvents')\n",
    ],
)
def test_exported_conflicting_and_uncertain_facade_bindings_are_preserved(
    tmp_path: Path, exports: str
) -> None:
    root = _project(tmp_path, exports="from .base import Browser\n" + exports)
    write_project(root, {"app/browser/api.py": "from .base import BrowserEvents\n"})
    assert violations(root, OPTIONS) == []


def test_static_all_gap_only_suggests_exposing_the_existing_binding(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        exports="from .base import Browser, BrowserEvents\n__all__ = ['Browser']\n",
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["needs_import"] is False
    assert finding.hint is not None
    assert "adding it to __all__" in finding.hint


def test_reexport_provenance_aggregates_aliases_and_shows_other_facades(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    write_project(
        root,
        {
            "app/other/__init__.py": "from app.browser.base import BrowserEvents as Events\n",
            "app/other/bridge.py": "from app.browser.base import BrowserEvents as Events\n",
            "app/events.py": "from app.browser.base import BrowserEvents as Events\n__all__ = ['Events']\n",
            "app/second.py": "from app.other.bridge import Events\n",
            "app/third.py": "from app.other.bridge import Events as MyEvents\n",
        },
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.path == "app/browser/base.py"
    assert finding.expected["name"] == "BrowserEvents"
    assert finding.expected["reexports"] == ["app.events.Events", "app.other.Events"]
    assert {"app.first", "app.second", "app.third"} <= set(
        finding.expected["source_importers"]
    )


@pytest.mark.parametrize(
    "definition",
    [
        "if enabled:\n    class BrowserEvents: pass\n",
        "from .base import BrowserEvents\n",
        "class BrowserEvents: pass\nBrowserEvents = object()\n",
    ],
)
def test_unknown_cyclic_and_rebound_provenance_is_not_a_definition(
    tmp_path: Path, definition: str
) -> None:
    root = _project(tmp_path)
    write_project(root, {"app/browser/base.py": "class Browser: pass\n" + definition})
    assert violations(root, OPTIONS) == []


def test_namespace_facade_is_not_proposed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "app/browser/__init__.py").unlink()
    assert violations(root, OPTIONS) == []


def test_cycle_check_reports_an_internal_return_path_even_without_external_dependencies(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path, exports="from .common import Marker\nfrom .base import Browser\n"
    )
    write_project(
        root,
        {
            "app/browser/base.py": "from .helper import Marker\nclass Browser: pass\nclass BrowserEvents: pass\n",
            "app/browser/helper.py": "from app.browser import Marker\n",
            "app/browser/common.py": "Marker = 1\n",
        },
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["cycle_check"] == "cycle"
    assert finding.expected["cycle"] == [
        "app.browser",
        "app.browser.base",
        "app.browser.helper",
        "app.browser",
    ]
    assert finding.expected["automatic_rewrite"] is False


def test_threshold_and_rule_off_and_missing_policy_are_respected(
    tmp_path: Path,
) -> None:
    for config in (
        CONFIG.replace("export_at: 3", "export_at: 4"),
        CONFIG.replace("export_at: 3", "required: [base]"),
        CONFIG + "rules: {facade-export-gap: off}\n",
    ):
        _project(tmp_path, config=config)
        assert violations(tmp_path, OPTIONS) == []


def test_warm_and_uncached_results_follow_callers_exports_and_deleted_definitions(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    assert len(violations(root, OPTIONS)) == 1
    for updates, expected in (
        ({"app/third.py": ""}, 0),
        ({"app/third.py": CLIENT}, 1),
        ({"app/browser/__init__.py": "from .base import Browser, BrowserEvents\n"}, 0),
        ({"app/browser/__init__.py": "from .base import Browser\n"}, 1),
        ({"app/browser/base.py": "class Browser: pass\n"}, 0),
    ):
        write_project(root, updates)
        warm = violations(root, OPTIONS)
        fresh = violations(root, CheckOptions(select=("SMT109",), use_cache=False))
        assert warm == fresh
        assert len(warm) == expected
    (root / "app/browser/base.py").unlink()
    assert violations(root, OPTIONS) == []


def test_recorded_gap_does_not_hide_a_new_symbol_or_importer(tmp_path: Path) -> None:
    root = _project(tmp_path)
    [old] = violations(root, OPTIONS)
    debt = Debt.from_violations([old], lambda _finding: "")
    write_project(root, {"app/fourth.py": CLIENT})
    [expanded] = violations(root, OPTIONS)
    new, known, _ = debt.match([expanded], lambda _finding: "")
    assert new == [expanded]
    assert len(known) == 1
    assert known[0].expected is not None
    assert old.expected is not None
    assert known[0].expected["export_edges"] == old.expected["export_edges"]
    write_project(
        root,
        {
            "app/browser/base.py": "class Browser: pass\nclass TabInfo: pass\n",
            **{
                f"app/{name}.py": "from app.browser.base import TabInfo\n"
                for name in ("first", "second", "third", "fourth")
            },
        },
    )
    [different] = violations(root, OPTIONS)
    assert debt.match([different], lambda _finding: "")[0] == [different]


@pytest.mark.parametrize("policy", ["export_at: 1", "count_tests: true"])
def test_invalid_export_policies_fail_clearly(tmp_path: Path, policy: str) -> None:
    root = _project(tmp_path, config=CONFIG.replace("export_at: 3", policy))
    with pytest.raises(
        ConfigError, match=r"greater than or equal to 2|count_tests requires export_at"
    ):
        violations(root, OPTIONS)


@pytest.mark.parametrize("threshold", [2, 4])
def test_export_threshold_uses_the_exact_contract_once(
    tmp_path: Path, threshold: int
) -> None:
    config = (
        CONFIG.replace("match: app.browser", "match: app.*")
        + f"\n    exact: {{match: app.browser, export_at: {threshold}}}\n"
    )
    root = _project(tmp_path, config=config)
    found = violations(root, OPTIONS)
    assert len(found) == (1 if threshold == 2 else 0)
    if found:
        assert found[0].expected is not None
        assert found[0].expected["contract"] == "exact"


@pytest.mark.parametrize("public", ["[]", "[base]"])
def test_explicitly_nonpublic_facades_do_not_receive_export_suggestions(
    tmp_path: Path, public: str
) -> None:
    root = _project(
        tmp_path,
        config=CONFIG.replace("export_at: 3", f"export_at: 3, public: {public}"),
    )
    assert violations(root, OPTIONS) == []


def test_source_test_packages_do_not_drive_exports_by_default(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        config=CONFIG.replace("test_roots: [tests]", "test_roots: [app/tests]"),
    )
    write_project(root, {"app/third.py": "", "app/tests/test_browser.py": CLIENT})
    assert violations(root, OPTIONS) == []
    write_project(
        root,
        {
            "smelt.yaml": CONFIG.replace(
                "test_roots: [tests]", "test_roots: [app/tests]"
            ).replace("export_at: 3", "export_at: 3, count_tests: true")
        },
    )
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert finding.expected["test_importers"] == ["app.tests.test_browser"]


def test_visibility_policy_in_a_separate_contract_is_respected(tmp_path: Path) -> None:
    root = _project(
        tmp_path, config=CONFIG + "\n    private: {match: app.browser, public: []}\n"
    )
    assert violations(root, OPTIONS) == []


def test_equal_specificity_export_conflicts_include_test_counting(
    tmp_path: Path,
) -> None:
    config = (
        CONFIG
        + "\n    duplicate: {match: app.browser, export_at: 3, count_tests: true}\n"
    )
    root = _project(tmp_path, config=config)
    with pytest.raises(ConfigError, match="conflicting export_at"):
        violations(root, OPTIONS)
