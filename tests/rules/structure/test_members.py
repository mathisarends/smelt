from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = """version: 1
project: {root_packages: [app]}
structure:
  contracts:
    helpers:
      match: app
      member_visibility: private-when-internal
      static_methods: module-functions
"""


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yaml": config, "app/__init__.py": "", "app/browser.py": source},
    )


def test_internal_async_public_method_including_callback_reference(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        """
        class Browser:
            async def emit(self, event):
                await send(event)
            async def run(self):
                await self.emit('ready')
                install(emit=self.emit)
        Browser().run()
        """,
    )
    [finding] = violations(root)
    assert (finding.code, finding.path, finding.line) == ("SMT316", "app/browser.py", 2)
    assert finding.severity == Severity.WARNING
    assert finding.expected is not None
    assert finding.expected["class"] == "Browser"
    assert finding.expected["suggested_name"] == "_emit"
    assert finding.expected["observed_usage"] == "internal-only"
    assert finding.expected["internal_references"] == [5, 6]
    assert finding.expected["automatic_rename"] is False


@pytest.mark.parametrize("signature", ["def emit(self)", "async def emit(this, /)"])
def test_unused_public_instance_methods_are_reviewed(
    tmp_path: Path, signature: str
) -> None:
    [finding] = violations(_project(tmp_path, f"class Browser:\n    {signature}: pass"))
    assert finding.code == "SMT316"
    assert finding.expected is not None
    assert finding.expected["observed_usage"] == "unused"


@pytest.mark.parametrize(
    "consumer",
    [
        "Browser().emit()",
        "browser = Browser()\nbrowser.emit()",
        "callback = Browser.emit",
        "def consume(browser): browser.emit()",
        "def consume(browser): return browser.emit",
        "def consume(browser): return getattr(browser, 'emit')",
        "class Other:\n    def run(self, browser): browser.emit()\nOther().run(None)",
    ],
)
def test_external_calls_callbacks_and_unknown_same_named_receivers_preserve_methods(
    tmp_path: Path, consumer: str
) -> None:
    root = _project(tmp_path, "class Browser:\n    def emit(self): pass\n" + consumer)
    assert not any(
        finding.expected and finding.expected.get("member") == "emit"
        for finding in violations(root)
    )


def test_method_accesses_from_tests_count_as_external_uses(tmp_path: Path) -> None:
    root = _project(tmp_path, "class Browser:\n    def emit(self): pass")
    write_project(
        root,
        {
            "tests/test_browser.py": "from app.browser import Browser\ndef test_emit(): Browser().emit()"
        },
    )
    assert violations(root) == []


@pytest.mark.parametrize(
    "body",
    [
        "def callback(other): return other.emit\n        return callback",
        "def callback(self): return self.emit\n        return callback",
        "return lambda self: self.emit",
        "return [self.emit for self in objects]",
        "self = external\n        return self.emit",
        "Browser = external\n        return Browser.emit",
    ],
)
def test_shadowed_and_unresolved_receivers_inside_class_helpers_preserve_visibility(
    tmp_path: Path, body: str
) -> None:
    root = _project(
        tmp_path,
        "class Browser:\n    def emit(self): pass\n    def run(self):\n        "
        + body
        + "\nBrowser().run()",
    )
    assert violations(root) == []


def test_nested_closure_keeps_the_owning_instance_reference_internal(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "class Browser:\n    def emit(self): pass\n    def run(self):\n        def callback(): return self.emit\n        return callback\nBrowser().run()",
    )
    [finding] = violations(root)
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_emit"
    assert finding.expected["observed_usage"] == "internal-only"


def test_existing_private_instance_attributes_are_not_suggested_as_rename_targets(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "class Browser:\n    def __init__(self, callback): self._emit = callback\n    def emit(self, event): return self._emit(event)\n    def run(self): return self.emit('ready')\nBrowser(None).run()",
    )
    [finding] = violations(root)
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_emit_internal"


@pytest.mark.parametrize(
    "source",
    [
        "class Browser:\n    def _emit(self): pass\n    def __str__(self): return 'browser'",
        "class Browser:\n    @property\n    def status(self): return 'ready'",
        "class Browser:\n    @classmethod\n    def open(cls): return cls()",
        "class Browser:\n    @register\n    def emit(self): pass",
        "@framework\nclass Browser:\n    def emit(self): pass",
        "class Browser(Protocol):\n    def emit(self): pass",
        "class Browser(Base):\n    def emit(self): pass",
        "class Browser(metaclass=Meta):\n    def emit(self): pass",
        "class Browser:\n    def emit(self): pass\nclass Child(Browser): pass",
        "class Browser:\n    def emit(self): pass\n    emit = callback",
        "class Browser:\n    def emit(self): pass\n    if condition:\n        def emit(self): pass",
        "def outer():\n    class Browser:\n        def emit(self): pass",
    ],
)
def test_private_special_decorated_inherited_and_uncertain_methods_are_preserved(
    tmp_path: Path, source: str
) -> None:
    root = _project(tmp_path, source)
    found = violations(root, CheckOptions(select=("SMT316",)))
    assert not any(
        finding.expected and finding.expected.get("kind") == "method"
        for finding in found
    )


def test_inheritance_through_facades_and_aliases_preserves_base_methods(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "class Browser:\n    def emit(self): pass")
    write_project(
        root,
        {
            "app/__init__.py": "from .browser import Browser as PublicBrowser",
            "app/child.py": "from app import PublicBrowser as Base\nclass Child(Base): pass",
        },
    )
    assert violations(root) == []


def test_generic_subclasses_preserve_base_method_visibility(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "class Browser[T]:\n    def emit(self): pass\nclass Child(Browser[int]): pass",
    )
    assert violations(root) == []


def test_local_public_module_helpers_and_unused_functions_are_reviewed(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "__all__ = ['run']\ndef normalize(value): return value.strip()\ndef unused(): pass\ndef run(value): return normalize(value)",
    )
    found = violations(root)
    assert [(finding.code, finding.line) for finding in found] == [
        ("SMT316", 2),
        ("SMT316", 3),
    ]
    assert [
        finding.expected["suggested_name"] for finding in found if finding.expected
    ] == ["_normalize", "_unused"]


@pytest.mark.parametrize(
    "consumer",
    [
        "from app.browser import normalize",
        "from .browser import normalize as clean",
        "import app.browser as browser\nvalue = browser.normalize('a')",
        "from app import browser\nvalue = browser.normalize('a')",
        "from app.browser import *",
        "import app.browser as browser\nconsume(browser)",
    ],
)
def test_module_function_consumers_preserve_visibility(
    tmp_path: Path, consumer: str
) -> None:
    root = _project(tmp_path, "def normalize(value): return value.strip()")
    write_project(root, {"app/consumer.py": consumer})
    assert violations(root) == []


@pytest.mark.parametrize(
    "source",
    [
        "__all__ = ['normalize']\ndef normalize(value): return value.strip()",
        "__all__ = exports()\ndef normalize(value): return value.strip()",
        "@register\ndef normalize(value): return value.strip()",
        "def _normalize(value): return value.strip()",
        "def normalize(value): return value.strip()\nnormalize = replacement",
    ],
)
def test_module_exports_and_decorated_private_or_rebound_functions_are_preserved(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source)) == []


def test_test_only_module_function_stays_with_smt429_without_duplicate_findings(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "def normalize(value): return value.strip()",
        CONFIG + "tests: {review_test_only_public_functions: true}\n",
    )
    write_project(root, {"tests/test_browser.py": "from app.browser import normalize"})
    [finding] = violations(root)
    assert finding.code == "SMT429"


@pytest.mark.parametrize(
    ("decorator", "imports"),
    [
        ("staticmethod", ""),
        ("builtins.staticmethod", "import builtins\n"),
        ("static", "from builtins import staticmethod as static\n"),
    ],
)
@pytest.mark.parametrize("keyword", ["def", "async def"])
def test_internal_static_helpers_prefer_private_module_functions(
    tmp_path: Path, decorator: str, imports: str, keyword: str
) -> None:
    root = _project(
        tmp_path,
        imports
        + f"class Browser:\n    @{decorator}\n    {keyword} normalize(value):\n        return value.strip()\n    def run(self, value):\n        return self.normalize(value)\nBrowser().run('a')",
    )
    [finding] = violations(root)
    assert finding.code == "SMT317"
    assert finding.severity == Severity.HINT
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_normalize"
    assert finding.expected["destination"] == "app.browser"
    assert finding.expected["automatic_extraction"] is False


def test_private_static_helper_can_be_unused_or_called_by_class_name(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "class Browser:\n    @staticmethod\n    def _normalize(value): return value.strip()\n    def run(self): return Browser._normalize('a')\nBrowser().run()",
    )
    [finding] = violations(root)
    assert finding.code == "SMT317"
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_normalize"
    assert finding.expected["internal_references"] == [4]


@pytest.mark.parametrize(
    "source",
    [
        "class Browser:\n    @staticmethod\n    def normalize(value): return value.strip()\nBrowser.normalize('a')",
        "class Browser:\n    @staticmethod\n    def normalize(value): return value.strip()\ncallback = Browser.normalize",
        "class Browser:\n    @staticmethod\n    @register\n    def normalize(value): return value.strip()",
        "class Browser:\n    @staticmethod\n    def normalize(value): return __class__.prefix + value",
        "class Browser:\n    @staticmethod\n    def normalize(value): return super().normalize(value)",
        "class Browser:\n    prefix = 'x'\n    @staticmethod\n    def normalize(value=prefix): return value",
        "class Browser:\n    prefix = 'x'\n    @staticmethod\n    def normalize(value): return Browser.prefix + value",
        "class Browser(Base):\n    @staticmethod\n    def normalize(value): return value.strip()",
        "class Browser:\n    @staticmethod\n    def normalize(value): return value.strip()\nclass Child(Browser): pass",
        "staticmethod = custom\nclass Browser:\n    @staticmethod\n    def normalize(value): return value.strip()",
        "class Browser:\n    staticmethod = custom\n    @staticmethod\n    def normalize(value): return value.strip()",
        "class Browser[T]:\n    @staticmethod\n    def normalize(value: T): return value",
        "class Browser:\n    class Options: pass\n    @staticmethod\n    def normalize(value: Options): return value",
    ],
)
def test_static_apis_decorators_class_dependencies_and_shadowed_decorators_are_preserved(
    tmp_path: Path, source: str
) -> None:
    assert (
        violations(_project(tmp_path, source), CheckOptions(select=("SMT317",))) == []
    )


def test_static_extraction_suggests_a_name_free_in_the_module_namespace(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "def _normalize(value): return value\nclass Browser:\n    @staticmethod\n    def _normalize(value): return value.strip()",
    )
    [finding] = violations(root)
    assert finding.code == "SMT317"
    assert finding.expected is not None
    assert finding.expected["suggested_name"] == "_normalize_internal"


@pytest.mark.parametrize("code", ["SMT316", "SMT317"])
@pytest.mark.parametrize(
    "config",
    [
        "version: 1\nproject: {root_packages: [app]}",
        CONFIG.replace("private-when-internal", "allow").replace(
            "module-functions", "allow"
        ),
    ],
)
def test_member_policies_are_opt_in_even_when_selected(
    tmp_path: Path, code: str, config: str
) -> None:
    root = _project(
        tmp_path,
        "class Browser:\n    def emit(self): pass\n    @staticmethod\n    def normalize(value): return value.strip()",
        config,
    )
    assert violations(root, CheckOptions(select=(code,))) == []


def test_closest_allow_overrides_apply_to_both_member_policies(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "",
        CONFIG
        + "    compat: {match: app.compat, member_visibility: allow, static_methods: allow}\n",
    )
    write_project(
        root,
        {
            "app/compat/__init__.py": "",
            "app/compat/browser.py": "class Browser:\n    def emit(self): pass\n    @staticmethod\n    def normalize(value): return value.strip()",
        },
    )
    assert violations(root) == []


@pytest.mark.parametrize(
    ("code", "source"),
    [
        (
            "SMT316",
            "class Browser:\n    def emit(self): pass  # smelt: ignore[SMT316] -- third-party callback contract",
        ),
        (
            "SMT317",
            "class Browser:\n    @staticmethod\n    def normalize(value): return value.strip()  # smelt: ignore[SMT317] -- intentional static API",
        ),
    ],
)
def test_reasoned_suppressions_preserve_deliberate_public_or_static_interfaces(
    tmp_path: Path, code: str, source: str
) -> None:
    outcome = check(_project(tmp_path, source), CheckOptions(select=(code,)))
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1
