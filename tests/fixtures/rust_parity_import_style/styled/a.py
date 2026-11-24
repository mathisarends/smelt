from typing import TYPE_CHECKING
from styled.b import one
from .b import two
import styled.b

if TYPE_CHECKING:
    from styled.b import guarded
    import styled.c as guarded_plain
elif False:
    from styled.b import not_guarded
