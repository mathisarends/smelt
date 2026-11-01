from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.lazy_exports import static_lazy_exports
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

LAZY = """
from importlib import import_module

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value

_EXPORTS = {"Public": ("app.api", "Service")}

def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))

__all__ = sorted(_EXPORTS)
"""


@pytest.mark.parametrize(
    "source",
    [
        LAZY,
        LAZY.replace("    globals()[name] = value\n", ""),
        LAZY.replace("from importlib import import_module", "import importlib").replace(
            "getattr(import_module(", "getattr(importlib.import_module("
        ),
        LAZY.replace("sorted(_EXPORTS)", "['Public']"),
        "from typing import Any\n"
        + LAZY.replace("def __getattr__(name):", "def __getattr__(name: str) -> Any:"),
    ],
)
def test_literal_forwarding_table_is_a_static_export(source: str) -> None:
    assert static_lazy_exports(ast.parse(source)) == {"Public": "app.api.Service"}


@pytest.mark.parametrize(
    "source",
    [
        LAZY.replace('("app.api", "Service")', "build_export()"),
        LAZY.replace('("app.api", "Service")', '(".api", "Service")'),
        LAZY.replace('("app.api", "Service")', '("app.api", "Service", "extra")'),
        LAZY.replace("return value", "return replacement"),
        LAZY.replace(
            "getattr(import_module(module), attribute)",
            "custom_loader(module, attribute)",
        ),
        LAZY.replace("if name not in _EXPORTS:", "if condition:"),
        LAZY.replace(
            "def __getattr__(name):", "def __getattr__(name: namespace.dynamic):"
        ),
        LAZY + '\n_EXPORTS["Public"] = ("app.other", "Other")\n',
        LAZY + "\n_EXPORTS.update(other)\n",
        LAZY + "\nother = _EXPORTS\n",
        LAZY + "\nimport_module = replacement\n",
        LAZY + "\ngetattr = replacement\n",
        LAZY + "\nPublic = replacement\n",
        LAZY + "\n__all__.clear()\n",
        LAZY + '\n__all__ += ["Other"]\n',
        LAZY.replace(
            "def __getattr__(name):", "def __getattr__(name: mutate(_EXPORTS)):\n"
        ),
        LAZY.replace(
            'raise AttributeError(f"module {__name__!r} has no attribute {name!r}")',
            "raise AttributeError(name) from mutate(_EXPORTS)",
        ),
        LAZY.replace("def __dir__():", "@mutate(_EXPORTS)\ndef __dir__():"),
        LAZY.replace("__all__ = sorted(_EXPORTS)", "__all__ = other = sorted(_EXPORTS)")
        + "\nother.clear()\n",
        LAZY.replace("module, attribute =", "getattr, attribute =").replace(
            "import_module(module)", "import_module(getattr)"
        ),
        LAZY.replace(
            'raise AttributeError(f"module {__name__!r} has no attribute {name!r}")',
            "raise AttributeError(_EXPORTS.clear())",
        ),
        LAZY.replace("def __getattr__(name):", "@decorate\ndef __getattr__(name):"),
        '_EXPORTS = {"Public": ("app.api", "Service")}\n',
        "def __getattr__(name): return build(name)\n",
    ],
)
def test_uncertain_tables_handlers_and_mutations_are_unverified(source: str) -> None:
    assert static_lazy_exports(ast.parse(source)) is None


def test_lazy_exports_protect_actual_api_and_invalidate_after_table_edits(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\nrules: {internal-public-member: warning, test-only-public-function: warning}\n",
            "app/__init__.py": LAZY,
            "app/__init__.pyi": "from .api import Service as Public\n",
            "app/api.py": "class Service:\n    def run(self): pass\n",
        },
    )
    assert check(tmp_path).report.violations == []
    write_project(
        tmp_path,
        {
            "app/__init__.py": LAZY.replace(
                '("app.api", "Service")', '("app.missing", "Service")'
            )
        },
    )
    cached = check(tmp_path).report.violations
    fresh = check(tmp_path, CheckOptions(use_cache=False)).report.violations
    assert cached == fresh
    assert [finding.code for finding in fresh] == ["SMT316"]


def test_stub_alone_does_not_prove_a_runtime_export(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\nrules: {internal-public-member: warning}\n",
            "app/__init__.py": "",
            "app/__init__.pyi": "from .api import Service as Public\n",
            "app/api.py": "class Service:\n    def run(self): pass\n",
        },
    )
    assert [finding.code for finding in check(tmp_path).report.violations] == ["SMT316"]


def test_verified_lazy_export_can_establish_canonical_ownership(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\nstructure: {canonical_exports: {app.api.Service: app.Public}}\n",
            "app/__init__.py": LAZY,
            "app/api.py": "class Service: pass\n",
        },
    )
    assert check(tmp_path).report.violations == []
