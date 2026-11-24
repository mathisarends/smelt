from app.service import SessionLog
from app.pkg import Local
from app.pkg.peer import Peer


def use(log: SessionLog, local: Local, peer: Peer):
    return log._images, local._field, peer._state
