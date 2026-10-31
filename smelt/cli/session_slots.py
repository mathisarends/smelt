"""OS-owned worker slots, released even when a worker crashes or is terminated."""

from __future__ import annotations

import contextlib
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_MAX_WORKERS = 4


@contextlib.contextmanager
def worker_slot(directory: Path) -> Iterator[bool]:
    slots = directory / "worker-slots"
    slots.mkdir(exist_ok=True)
    with contextlib.ExitStack() as cleanup:
        for number in range(_MAX_WORKERS):
            stream = (slots / str(number)).open("a+b")
            try:
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                if sys.platform == "win32":
                    import msvcrt  # noqa: PLC0415 - platform-specific OS lock

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl  # noqa: PLC0415 - platform-specific OS lock

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                stream.close()
                continue
            cleanup.callback(stream.close)
            yield True
            return
        yield False
