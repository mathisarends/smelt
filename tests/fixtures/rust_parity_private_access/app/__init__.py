"""The application facade."""
from .service import _normalize as normalize
from .service import _counter as _counter
from .service import _cache
from .internal import _helper as helper

if True:
    from .internal import _helper as nested_public


def lazy():
    from .internal import _helper as local_public

    return local_public
