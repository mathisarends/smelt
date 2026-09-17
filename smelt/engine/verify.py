import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from smelt.config.models import VerifyStep


@dataclass(frozen=True, slots=True)
class StepResult:
    name: str
    command: str
    status: str  # passed | failed | skipped
    exit_code: int | None = None
    duration: float = 0.0
    output: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration": round(self.duration, 3),
            "output": self.output,
        }


def run_steps(
    steps: list[VerifyStep],
    root: Path,
    *,
    fail_fast: bool = False,
    on_result: Callable[[StepResult], None] | None = None,
) -> list[StepResult]:
    """Run each configured command in order from the project root."""
    results: list[StepResult] = []
    failed = False
    for step in steps:
        if failed and fail_fast:
            result = StepResult(step.name, step.run, "skipped")
        else:
            started = time.perf_counter()
            try:
                completed = subprocess.run(  # noqa: S602 - commands come from smelt.yaml
                    step.run,
                    shell=True,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                code, output = completed.returncode, completed.stdout + completed.stderr
            except OSError as exc:
                code, output = 127, str(exc)
            status = "passed" if code == 0 else "failed"
            failed = failed or code != 0
            result = StepResult(
                step.name,
                step.run,
                status,
                code,
                time.perf_counter() - started,
                output,
            )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
