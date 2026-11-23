from typing import TYPE_CHECKING

import parity.import_graph, parity.import_graph.leaf
import parity.import_graph.missing
from parity import import_graph
from parity.import_graph import leaf, namespace, nothing
from parity.import_graph.namespace import inner as inner_alias, deeper
from parity.import_graph.leaf import *
from . import conftest
from .nested import helpers
from external_pkg import thing

if TYPE_CHECKING:
    from parity.import_graph import (
        guarded,
        other,
    )


def test_local_import():
    from parity.import_graph.other import value

    assert value
