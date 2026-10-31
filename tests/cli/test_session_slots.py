from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from smelt.cli.session_slots import worker_slot

if TYPE_CHECKING:
    from pathlib import Path


def test_worker_slots_are_released_after_an_owned_child_crashes(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    script = (
        "import sys,time,json,os\nfrom pathlib import Path\n"
        "from smelt.cli.session_slots import worker_slot\n"
        "with worker_slot(Path(sys.argv[1])) as available:\n"
        " Path(sys.argv[2]).write_text(json.dumps({'available':available,'pid':os.getpid()}))\n"
        " time.sleep(60)\n"
    )
    with contextlib.ExitStack() as slots:
        for _ in range(3):
            assert slots.enter_context(worker_slot(tmp_path))
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter and owned child
            [sys.executable, "-P", "-c", script, str(tmp_path), str(ready)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child_pid: int | None = None
        try:
            deadline = time.monotonic() + 5
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            metadata = json.loads(ready.read_text())
            assert metadata["available"]
            child_pid = metadata["pid"]
            with worker_slot(tmp_path) as available:
                assert not available
            os.kill(child_pid, signal.SIGTERM)
            process.wait(timeout=5)
            with worker_slot(tmp_path) as available:
                assert available
        finally:
            if process.poll() is None:
                if child_pid is not None:
                    with contextlib.suppress(OSError):
                        os.kill(child_pid, signal.SIGTERM)
                else:
                    process.kill()
            process.wait(timeout=5)
