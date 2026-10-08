from __future__ import annotations

import ast
import textwrap

import pytest

from smelt.analysis.exports import (
    Loader,
    Namespace,
    namespace_of,
    resolve_dotted,
    resolve_export,
)

PACKAGES = {"app", "app.billing", "app.cycle", "app.cycle.inner"}


def _loader(sources: dict[str, str]) -> Loader:
    def load(module: str) -> Namespace | None:
        if module not in sources:
            return None
        tree = ast.parse(textwrap.dedent(sources[module]))
        return namespace_of(tree, module, is_package=module in PACKAGES)

    return load


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
        self, source: str, name: str, expected: tuple[str, str | None]
    ) -> None:
        load = _loader({**SOURCES, "app": source})

        assert resolve_export("app", name, load) == expected
        namespace = load("app")
        assert namespace is not None
        assert not namespace.defined.intersection(namespace.imported)
        assert set(namespace.references) == namespace.defined

    def test_follows_aliases_through_facades(self) -> None:
        load = _loader(SOURCES)

        assert resolve_export("app", "Bill", load) == ("app.billing.models", "Invoice")

    def test_submodule_is_a_module(self) -> None:
        load = _loader(SOURCES)

        assert resolve_export("app.billing", "models", load) == (
            "app.billing.models",
            None,
        )

    def test_cycle_ends(self) -> None:
        load = _loader(SOURCES)

        assert resolve_export("app.cycle", "Thing", load) is None

    def test_third_party_is_unresolved(self) -> None:
        load = _loader({"app": "from pydantic import BaseModel\n"})

        assert resolve_export("app", "BaseModel", load) is None


class TestResolveDotted:
    def test_attribute_of_an_imported_package(self) -> None:
        load = _loader(SOURCES)

        assert resolve_dotted("app.billing.Invoice", load) == (
            "app.billing.models",
            "Invoice",
        )

    def test_module_part_is_imported_not_looked_up_as_attribute(self) -> None:
        # app/__init__.py binds ``billing``; ``app.billing.models`` is still the module
        load = _loader({**SOURCES, "app": "billing = 1\n"})

        assert resolve_dotted("app.billing.models.Invoice", load) == (
            "app.billing.models",
            "Invoice",
        )

    def test_stops_at_an_object(self) -> None:
        load = _loader(SOURCES)

        assert resolve_dotted("app.billing.models.TOTAL.amount", load) == (
            "app.billing.models",
            "TOTAL",
        )


class TestNamespace:
    def test_definitions_record_the_names_they_read(self) -> None:
        tree = ast.parse("from . import chat\nFEATURES = (chat.feature, extra)\n")

        namespace = namespace_of(tree, "app.features", is_package=True)

        assert namespace.imported == {"chat": "app.features.chat"}
        assert ("chat", "feature") in namespace.references["FEATURES"]
        assert ("extra",) in namespace.references["FEATURES"]
