import parity.import_graph.other, os.path
import parity.import_graph.other as other_alias
from typing import TYPE_CHECKING

value = 1

if TYPE_CHECKING:
    from parity.import_graph.guarded import Guarded
    import parity.import_graph.other
elif other_alias.TYPE_CHECKING:
    from parity.import_graph.guarded import Elif
else:
    from parity.import_graph.guarded import Runtime
