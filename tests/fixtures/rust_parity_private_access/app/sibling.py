from .service import _normalize, SessionLog
from . import service


def run(log: SessionLog):
    return _normalize(log), service._cache, log._images
