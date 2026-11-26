"""Declarations supported by local rule modules, read from the policy snapshot."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

from smelt.config import calls
from smelt.config.errors import ConfigError, ConfigIssue

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.config.source import SourceStore


@dataclass(frozen=True, slots=True, kw_only=True)
class Rule:
    name: str
    match: calls.CallMatcher
    message: str
    guide: str


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
            exported = _declarations(store.read_text(path))
            for rule in exported:
                if rule.name in origins:
                    raise ValueError(
                        f"duplicate rule {rule.name!r}, first declared in {origins[rule.name]}"
                    )
                origins[rule.name] = relative
                rules.append(rule)
        except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
            raise ConfigError(
                [ConfigIssue("plugins", f"{relative}: {exc}")], str(owner)
            ) from exc
    return tuple(rules)


def _declarations(source: str) -> list[Rule]:
    declared: dict[str, Rule] = {}
    imported: set[str] = set()
    exported: list[Rule] | None = None
    for node in ast.parse(source).body:
        match node:
            case ast.Expr(value=ast.Constant(value=str())):
                continue
            case ast.ImportFrom(module="smelt.rules", level=0, names=names):
                for alias in names:
                    if alias.name not in {"Rule", "calls"} or alias.asname:
                        raise ValueError(
                            f"line {node.lineno}: import Rule and calls by name"
                        )
                    imported.add(alias.name)
            case ast.Assign(targets=[ast.Name(id="rules")], value=ast.List(elts=items)):
                if exported is not None:
                    raise ValueError(f"line {node.lineno}: duplicate rules export")
                exported = [_export(item, declared) for item in items]
            case ast.Assign(
                targets=[ast.Name(id=name)],
                value=ast.Call(func=ast.Name(id="Rule")) as call,
            ):
                if (
                    imported != {"Rule", "calls"}
                    or name in declared
                    or name in {"Rule", "calls", "rules"}
                ):
                    raise ValueError(
                        f"line {node.lineno}: expected a fresh rule name after importing Rule and calls"
                    )
                declared[name] = _rule(call)
            case _:
                raise ValueError(
                    f"line {node.lineno}: expected a rule declaration or rules export"
                )
    if exported is None:
        raise ValueError("missing rules export list")
    return exported


def _export(item: ast.expr, declared: dict[str, Rule]) -> Rule:
    if isinstance(item, ast.Name) and item.id in declared:
        return declared[item.id]
    raise ValueError(f"line {item.lineno}: expected a previously declared rule")


def _rule(call: ast.Call) -> Rule:
    fields = {kw.arg: kw.value for kw in call.keywords}
    if (
        call.args
        or len(fields) != len(call.keywords)
        or fields.keys() != {"name", "match", "message", "guide"}
    ):
        raise ValueError(
            f"line {call.lineno}: Rule expects name, match, message and guide keywords"
        )
    name = _text(fields["name"])
    if not re.fullmatch(
        r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*/[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name
    ):
        raise ValueError(
            f"line {call.lineno}: expected a qualified rule name such as acme/domain-clock"
        )
    return Rule(
        name=name,
        match=_matcher(fields["match"]),
        message=_text(fields["message"]),
        guide=_text(fields["guide"]),
    )


def _matcher(node: ast.expr) -> calls.CallMatcher:
    layer = None
    match node:
        case ast.Call(
            func=ast.Attribute(value=base, attr="in_layer"),
            args=[argument],
            keywords=[],
        ):
            node = base
            layer = _text(argument)
    match node:
        case ast.Call(
            func=ast.Attribute(value=ast.Name(id="calls"), attr="to"),
            args=[argument],
            keywords=[],
        ):
            target = _text(argument)
            if "." in target and all(part.isidentifier() for part in target.split(".")):
                match = calls.to(target)
                return match.in_layer(layer) if layer is not None else match
    raise ValueError(
        f"line {node.lineno}: expected calls.to('module.name') with optional .in_layer('layer')"
    )


def _text(node: ast.expr) -> str:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip()
    ):
        return node.value
    raise ValueError(f"line {node.lineno}: expected a non-empty string literal")
