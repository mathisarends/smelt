from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.symbols import Loader, Namespace, read_symbols, resolve_dotted
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

PACKAGES = {"app", "app.billing", "app.cycle", "app.cycle.inner"}


def _loader(root: Path, sources: dict[str, str]) -> Loader:
    """The namespaces the native core reads from ``sources``, by module."""
    files = []
    for module, source in sources.items():
        name = module.replace(".", "/")
        path = root / (f"{name}/__init__.py" if module in PACKAGES else f"{name}.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        files.append((path, module))
    found = read_symbols(files)
    namespaces = {
        module: None if (symbols := found[path]) is None else symbols.namespace
        for path, module in files
    }
    return namespaces.get


def _namespace(root: Path, source: str) -> Namespace:
    namespace = _loader(root, {"app": source})("app")
    assert namespace is not None
    return namespace


SOURCES = {
    "app": "from .billing import Invoice as Bill\nfrom . import billing\n",
    "app.billing": "from .models import Invoice\nfrom . import models\n",
    "app.billing.models": "class Invoice: ...\nTOTAL = Invoice()\n",
    "app.cycle": "from .inner import Thing\n",
    "app.cycle.inner": "from app.cycle import Thing\n",
}


class TestResolveExport:
    @pytest.mark.parametrize(
        ("source", "name", "expected"),
        [
            (
                "Invoice = None\nfrom .billing.models import Invoice\n",
                "Invoice",
                ("app.billing.models", "Invoice"),
            ),
            (
                "def models(): pass\nimport app.billing.models as models\n",
                "models",
                ("app.billing.models", None),
            ),
            (
                "app = None\nimport app.billing.models\n",
                "app",
                ("app", None),
            ),
            (
                "from .billing.models import Invoice\nInvoice = None\n",
                "Invoice",
                ("app", "Invoice"),
            ),
        ],
    )
    def test_latest_binding_wins(
        self,
        tmp_path: Path,
        source: str,
        name: str,
        expected: tuple[str, str | None],
    ) -> None:
        load = _loader(tmp_path, {**SOURCES, "app": source})

        assert resolve_dotted(f"app.{name}", load) == expected
        namespace = load("app")
        assert namespace is not None
        assert not namespace.defined.intersection(namespace.imported)
        assert set(namespace.references) == namespace.defined

    def test_follows_aliases_through_facades(self, tmp_path: Path) -> None:
        load = _loader(tmp_path, SOURCES)

        assert resolve_dotted("app.Bill", load) == ("app.billing.models", "Invoice")

    def test_submodule_is_a_module(self, tmp_path: Path) -> None:
        load = _loader(tmp_path, SOURCES)

        assert resolve_dotted("app.billing.models", load) == (
            "app.billing.models",
            None,
        )

    def test_cycle_ends(self, tmp_path: Path) -> None:
        load = _loader(tmp_path, SOURCES)

        assert resolve_dotted("app.cycle.Thing", load) is None

    def test_third_party_is_unresolved(self, tmp_path: Path) -> None:
        load = _loader(tmp_path, {"app": "from pydantic import BaseModel\n"})

        assert resolve_dotted("app.BaseModel", load) is None


class TestResolveDotted:
    def test_attribute_of_an_imported_package(self, tmp_path: Path) -> None:
        load = _loader(tmp_path, SOURCES)

        assert resolve_dotted("app.billing.Invoice", load) == (
            "app.billing.models",
            "Invoice",
        )

    def test_module_part_is_imported_not_looked_up_as_attribute(
        self, tmp_path: Path
    ) -> None:
        # app/__init__.py binds ``billing``; ``app.billing.models`` is still the module
        load = _loader(tmp_path, {**SOURCES, "app": "billing = 1\n"})

        assert resolve_dotted("app.billing.models.Invoice", load) == (
            "app.billing.models",
            "Invoice",
        )

    def test_stops_at_an_object(self, tmp_path: Path) -> None:
        load = _loader(tmp_path, SOURCES)

        assert resolve_dotted("app.billing.models.TOTAL.amount", load) == (
            "app.billing.models",
            "TOTAL",
        )


class TestNamespace:
    def test_definitions_record_the_names_they_read(self, tmp_path: Path) -> None:
        namespace = _namespace(
            tmp_path, "from . import chat\nFEATURES = (chat.feature, extra)\n"
        )

        assert namespace.imported == {"chat": "app.chat"}
        assert ("chat", "feature") in namespace.references["FEATURES"]
        assert ("extra",) in namespace.references["FEATURES"]


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
        LAZY.replace("from importlib import import_module", "import importlib").replace(
            "getattr(import_module(", "getattr(importlib.import_module("
        ),
        LAZY.replace("sorted(_EXPORTS)", "['Public']"),
        "from typing import Any\n"
        + LAZY.replace("def __getattr__(name):", "def __getattr__(name: str) -> Any:"),
    ],
)
def test_literal_forwarding_table_is_a_static_export(
    tmp_path: Path, source: str
) -> None:
    assert _namespace(tmp_path, source).imported["Public"] == "app.api.Service"


@pytest.mark.parametrize(
    "source",
    [
        LAZY.replace('("app.api", "Service")', "build_export()"),
        LAZY.replace('("app.api", "Service")', '(".api", "Service")'),
        LAZY.replace("return value", "return replacement"),
        LAZY.replace("if name not in _EXPORTS:", "if condition:"),
        LAZY.replace(
            "def __getattr__(name):", "def __getattr__(name: namespace.dynamic):"
        ),
        LAZY + '\n_EXPORTS["Public"] = ("app.other", "Other")\n',
        LAZY + "\n_EXPORTS.update(other)\n",
        LAZY + "\nimport_module = replacement\n",
        LAZY + "\ngetattr = replacement\n",
        LAZY + "\nPublic = replacement\n",
        LAZY + '\n__all__ += ["Other"]\n',
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
def test_uncertain_tables_handlers_and_mutations_are_unverified(
    tmp_path: Path, source: str
) -> None:
    assert "Public" not in _namespace(tmp_path, source).imported


def test_lazy_exports_protect_only_the_api_their_table_resolves(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {visibility/internal-public-member: warning, visibility/test-only-public-function: warning}\n",
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
    found = check(tmp_path).report.violations
    assert [finding.code for finding in found] == ["VIS001"]


def test_stub_alone_does_not_prove_a_runtime_export(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\nrules: {visibility/internal-public-member: warning}\n",
            "app/__init__.py": "",
            "app/__init__.pyi": "from .api import Service as Public\n",
            "app/api.py": "class Service:\n    def run(self): pass\n",
        },
    )
    assert [finding.code for finding in check(tmp_path).report.violations] == ["VIS001"]


def test_verified_lazy_export_can_establish_canonical_ownership(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\narchitecture: {canonical_exports: {app.api.Service: app.Public}}\n",
            "app/__init__.py": LAZY,
            "app/api.py": "class Service: pass\n",
        },
    )
    assert check(tmp_path).report.violations == []
