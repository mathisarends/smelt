"""Rules a project defines in local Python modules, run from the policy snapshot."""

from __future__ import annotations

import re
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from importlib.abc import SourceLoader
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

from smelt.config.calls import CallMatcher
from smelt.config.errors import ConfigError, ConfigIssue

if TYPE_CHECKING:
    from smelt.config.source import SourceStore

_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*/[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


@dataclass(frozen=True, slots=True, kw_only=True)
class Rule:
    name: str
    match: CallMatcher
    message: str
    guide: str

    def __post_init__(self) -> None:
        _require_name(self.name)
        _require_matcher(self.name, self.match)
        _require_text(self.name, "message", self.message)
        _require_text(self.name, "guide", self.guide)


def load_project_rules(
    paths: list[str], owner: Path, *, root: Path, store: SourceStore
) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    origins: dict[str, str] = {}
    for relative in paths:
        try:
            path = store.resolve(owner.parent / relative)
            if (
                PureWindowsPath(relative).anchor
                or path.suffix != ".py"
                or not path.is_relative_to(root)
            ):
                raise ValueError("expected a project-relative .py file")
            if not store.is_file(path):
                raise ValueError("file not found")
            for rule in _exported(path, store):
                if rule.name in origins:
                    raise ValueError(
                        f"duplicate rule {rule.name!r}, first declared in {origins[rule.name]}"
                    )
                origins[rule.name] = relative
                rules.append(rule)
        except (OSError, ValueError) as exc:
            raise ConfigError(
                [ConfigIssue("plugins", f"{relative}: {exc}")], str(owner)
            ) from exc
    return tuple(rules)


def _require_name(name: object) -> None:
    if not (isinstance(name, str) and _NAME.fullmatch(name)):
        raise ValueError(
            f"expected a qualified rule name such as 'acme/domain-clock', got {name!r}"
        )


def _require_matcher(name: str, match: object) -> None:
    if not isinstance(match, CallMatcher):
        raise TypeError(f"{name}: expected match=calls.to(...), got {match!r}")


def _require_text(name: str, label: str, text: object) -> None:
    if not (isinstance(text, str) and text.strip()):
        raise ValueError(f"{name}: expected a non-empty {label}")


class _SnapshotLoader(SourceLoader):
    """Import one module from the policy snapshot; nothing is cached on disk."""

    def __init__(self, path: Path, store: SourceStore) -> None:
        self._path = path
        self._store = store

    def get_filename(self, fullname: str) -> str:
        return str(self._path)

    def get_data(self, path: str) -> bytes:
        return self._store.read_bytes(Path(path))


def _exported(path: Path, store: SourceStore) -> list[Rule]:
    loader = _SnapshotLoader(path, store)
    spec = ModuleSpec(f"smelt_project_rules_{path.stem}", loader, origin=str(path))
    spec.has_location = True
    module = module_from_spec(spec)
    try:
        # A report on stdout stays parseable when a rule module prints.
        with redirect_stdout(sys.stderr):
            loader.exec_module(module)
    except Exception as exc:  # Project code can fail in any way; report it as policy.
        raise ValueError(_failure(exc, path)) from exc
    exported = getattr(module, "rules", None)
    if not isinstance(exported, list | tuple) or not all(
        isinstance(rule, Rule) for rule in exported
    ):
        raise ValueError("expected a module-level `rules` list of Rule objects")
    return list(exported)


def _failure(exc: Exception, path: Path) -> str:
    line = _line(exc, path)
    place = f"line {line}: " if line is not None else ""
    return f"{place}{type(exc).__name__}: {exc}"


def _line(exc: Exception, path: Path) -> int | None:
    """The innermost line of the rule module itself on the way to the failure."""
    line = (
        exc.lineno
        if isinstance(exc, SyntaxError) and exc.filename == str(path)
        else None
    )
    traceback = exc.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename == str(path):
            line = traceback.tb_lineno
        traceback = traceback.tb_next
    return line
