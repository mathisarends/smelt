import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from smelt.config.models import SmeltConfig

PROBE_MODULE = "smelt_probe"
TIMEOUT_SECONDS = 180

type Pair = tuple[str, str]


class TypeIndex(Protocol):
    """Optional type information; rules must work when it is None."""

    def resolve_type(self, path: str, line: int, column: int) -> str | None: ...

    def implements(self, cls: str, protocol: str) -> bool: ...

    def prepare(self, pairs: Iterable[Pair]) -> None:
        """Announce the ``implements`` questions ahead, so they can be batched."""


@dataclass(frozen=True, slots=True)
class _Probe:
    """One `is a C assignable to a P?` question and the lines that answer it."""

    pair: Pair
    lines: list[str]

    @property
    def usable(self) -> bool:
        return bool(self.lines)


@dataclass
class PyrightTypes:
    """A ``TypeIndex`` that asks pyright whether one type is assignable to another.

    Every question becomes a tiny function in a generated module that pyright
    type-checks in one run; an error on those lines means the answer is no.
    """

    root: Path
    source_roots: tuple[str, ...]
    command: tuple[str, ...]
    timeout: int = TIMEOUT_SECONDS
    _answers: dict[Pair, bool] = field(default_factory=dict, repr=False)

    @classmethod
    def discover(cls, root: Path, config: SmeltConfig) -> PyrightTypes | None:
        """The backend, or None when pyright is not installed."""
        command = config.analysis.pyright_command
        if not command:
            return None
        executable = shutil.which(command[0])
        if executable is None:
            return None
        return cls(
            root=root,
            source_roots=tuple(config.project.source_roots),
            command=(executable, *command[1:]),
        )

    def resolve_type(self, path: str, line: int, column: int) -> str | None:
        """Always None: the command-line backend cannot resolve an expression."""
        del path, line, column
        return None

    def prepare(self, pairs: Iterable[Pair]) -> None:
        """Answer every unknown pair in a single pyright run."""
        unknown = [pair for pair in dict.fromkeys(pairs) if pair not in self._answers]
        if unknown:
            self._answers.update(self._ask(unknown))

    def implements(self, cls: str, protocol: str) -> bool:
        self.prepare([(cls, protocol)])
        return self._answers.get((cls, protocol), False)

    def _ask(self, pairs: list[Pair]) -> dict[Pair, bool]:
        probes = [_probe(pair, index) for index, pair in enumerate(pairs)]
        source, spans = _render(probes)
        with tempfile.TemporaryDirectory(prefix="smelt-types-") as directory:
            workspace = Path(directory)
            (workspace / f"{PROBE_MODULE}.py").write_text(
                source, encoding="utf-8", newline="\n"
            )
            (workspace / "pyrightconfig.json").write_text(
                json.dumps(self.pyright_config(), indent=2), encoding="utf-8"
            )
            output = self._run(workspace)
        if output is None:
            return dict.fromkeys(pairs, False)
        failed = _failing_lines(output)
        return {
            probe.pair: probe.usable and not (failed & set(span))
            for probe, span in zip(probes, spans, strict=True)
        }

    def pyright_config(self) -> dict[str, object]:
        return {
            "include": [f"{PROBE_MODULE}.py"],
            "extraPaths": [
                str((self.root / source_root).resolve())
                for source_root in self.source_roots
            ],
            "typeCheckingMode": "basic",
            "reportMissingModuleSource": "none",
        }

    def _run(self, workspace: Path) -> str | None:
        try:
            result = subprocess.run(  # noqa: S603 - the command comes from the config
                [*self.command, "--outputjson", "--project", str(workspace)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                cwd=workspace,
            )
        except OSError, subprocess.SubprocessError:
            return None
        return result.stdout


def _probe(pair: Pair, index: int) -> _Probe:
    """The lines that ask pyright whether ``pair[0]`` is assignable to ``pair[1]``."""
    subject, protocol = pair
    left, right = _split(subject), _split(protocol)
    if left is None or right is None:
        return _Probe(pair, [])
    return _Probe(
        pair,
        [
            f"from {left[0]} import {left[1]} as _subject{index}",
            f"from {right[0]} import {right[1]} as _protocol{index}",
            "",
            f"def _probe{index}(value: _subject{index}) -> _protocol{index}:",
            "    return value",
        ],
    )


def _split(qualname: str) -> tuple[str, str] | None:
    module, _, name = qualname.rpartition(".")
    return (module, name) if module and name.isidentifier() else None


def _render(probes: Sequence[_Probe]) -> tuple[str, list[list[int]]]:
    """The probe module, plus the line numbers each probe occupies."""
    lines = ["# generated by smelt; pyright reads it, nothing imports it", ""]
    spans: list[list[int]] = []
    for probe in probes:
        start = len(lines) + 1
        lines.extend(probe.lines)
        lines.append("")
        spans.append(list(range(start, len(lines) + 1)))
    return "\n".join(lines) + "\n", spans


def _failing_lines(output: str) -> set[int]:
    """The 1-based lines pyright reported an error on."""
    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        return set()
    diagnostics = document.get("generalDiagnostics", [])
    return {
        diagnostic["range"]["start"]["line"] + 1
        for diagnostic in diagnostics
        if diagnostic.get("severity") == "error" and "range" in diagnostic
    }
