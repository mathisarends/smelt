from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.parsing import resolve_relative
from smelt.diagnostics.violation import FileWrite, Fix, LineEdit

if TYPE_CHECKING:
    from collections.abc import Container, Iterable, Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.syntax import ClassInfo, ModuleSyntax
    from smelt.diagnostics.violation import Edit

INDENT = "    "
_TYPING_IMPORT = "from typing import TYPE_CHECKING"


def move_class_fix(
    ctx: AnalysisContext, cls: ClassInfo, target_module: str
) -> Fix | None:
    """Move a class into ``target_module``, or None if it cannot be done statically."""
    source = ctx.syntax.for_path(cls.path)
    if source is None or source.module == target_module:
        return None
    target_path = ctx.files.path_for_module(target_module) or ctx.files.module_to_path(
        target_module
    )
    if target_path == cls.path:
        return None
    return _Move(ctx, cls, source, target_module, target_path).build()


@dataclass(frozen=True, slots=True)
class _Import:
    """One name a module binds through a top-level import."""

    name: str
    node: ast.Import | ast.ImportFrom
    alias: ast.alias
    type_checking: bool


@dataclass(frozen=True, slots=True)
class _Rendered:
    """An import the target module needs, spelled the way it will be written."""

    name: str
    text: str
    type_checking: bool


class _Move:
    def __init__(
        self,
        ctx: AnalysisContext,
        cls: ClassInfo,
        source: ModuleSyntax,
        target_module: str,
        target_path: str,
    ) -> None:
        self.ctx = ctx
        self.cls = cls
        self.source = source
        self.target_module = target_module
        self.target_path = target_path
        self.lines = ctx.files.lines(cls.path)
        self.imports = dict(_module_imports(source))

    def build(self) -> Fix | None:
        span = self._span()
        if span is None:
            return None
        needed = self._needed(span)
        if needed is None:
            return None
        body = self.lines[span[0] - 1 : span[1]]
        content = self._target_content(body, needed)
        if content is None:
            return None
        rewrites = self._rewrites()
        if rewrites is None:
            return None
        edits: list[Edit] = [
            *self._source_edits(span, needed),
            FileWrite(self.target_path, content),
            *rewrites,
        ]
        return Fix(f"move {self.cls.name} to {self.target_path}", tuple(edits))

    def _span(self) -> tuple[int, int] | None:
        """The line range the class owns, comments and decorators included."""
        node = self.cls.node
        if node.col_offset != 0 or node.end_lineno is None:
            return None
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        while start > 1 and self.lines[start - 2].lstrip().startswith("#"):
            start -= 1
        end = node.end_lineno
        for other in self.source.tree.body:
            if other is node or other.end_lineno is None:
                continue
            if other.lineno <= end and other.end_lineno >= start:
                return None
        return start, end

    def _needed(self, span: tuple[int, int]) -> list[_Import] | None:
        """The imports the class relies on, or None if something cannot move with it."""
        if _used_outside(self.lines, span, self.cls.name):
            return None
        needed = []
        for name in sorted(_free_names(self.cls.node) - {self.cls.name}):
            found = self.imports.get(name)
            if found is not None:
                needed.append(found)
            elif name in self.source.definitions or name in self.source.bindings:
                return None
        return needed

    def _render(self, imp: _Import) -> str | None:
        """The import as the target module would have to spell it."""
        if isinstance(imp.node, ast.Import):
            return f"import {_alias_text(imp.alias)}"
        base = resolve_relative(
            self.source.module,
            is_package=self.source.is_package,
            level=imp.node.level,
            target=imp.node.module,
        )
        if not base or base == self.target_module:
            return None
        return f"from {base} import {_alias_text(imp.alias)}"

    def _target_content(self, body: list[str], needed: list[_Import]) -> str | None:
        rendered: list[_Rendered] = []
        for imp in needed:
            render = self._render(imp)
            if render is None:
                return None
            rendered.append(_Rendered(imp.name, render, imp.type_checking))
        existing = self.ctx.syntax.for_path(self.target_path)
        if existing is None:
            return _new_module(body, rendered)
        if self.cls.name in existing.definitions or self.cls.name in existing.bindings:
            return None
        return _extend_module(self.ctx, existing, body, rendered)

    def _source_edits(
        self, span: tuple[int, int], needed: list[_Import]
    ) -> list[LineEdit]:
        """Delete the class where it was, and the imports only it used."""
        dropped = set(range(span[0], span[1] + 1))
        dropped |= _surrounding_blanks(self.lines, span)
        stale = self._unused_aliases(span, needed)
        blocks = self._emptied_blocks(stale)
        typing = self.imports.get("TYPE_CHECKING")
        if blocks and typing is not None:
            ignore = {imp.node.lineno for imp in stale} | blocks | {typing.node.lineno}
            if not _used_outside(self.lines, span, typing.name, ignore=ignore):
                stale.append(typing)
        edits = [LineEdit(self.cls.path, line, None) for line in dropped | blocks]
        edits += self._alias_edits(stale)
        if self._leaves_nothing(edits):
            edits = [
                LineEdit(self.cls.path, line, None)
                for line in range(1, len(self.lines) + 1)
            ]
        return sorted(edits, key=lambda edit: edit.line)

    def _leaves_nothing(self, edits: list[LineEdit]) -> bool:
        """Whether the module would be left with nothing but blank lines."""
        replaced = {edit.line: edit.text for edit in edits}
        return not any(
            (replaced.get(number, line) or "").strip()
            for number, line in enumerate(self.lines, start=1)
        )

    def _unused_aliases(
        self, span: tuple[int, int], needed: list[_Import]
    ) -> list[_Import]:
        """Imports the moved class used and nothing else in the module does."""
        stale = []
        for imp in needed:
            line = self.lines[imp.node.lineno - 1]
            if imp.node.lineno != imp.node.end_lineno or "#" in line:
                continue
            ignore = {imp.node.lineno}
            if not _used_outside(self.lines, span, imp.name, ignore=ignore):
                stale.append(imp)
        return stale

    def _emptied_blocks(self, stale: list[_Import]) -> set[int]:
        """``if TYPE_CHECKING:`` headers whose whole body is about to disappear."""
        gone = {
            lineno
            for lineno, group in _by_line(stale).items()
            if not _kept_aliases(group)
        }
        return {
            block.lineno
            for block in _type_checking_blocks(self.source.tree)
            if block.body and {s.lineno for s in block.body} <= gone
        }

    def _alias_edits(self, stale: list[_Import]) -> list[LineEdit]:
        edits = []
        for lineno, group in _by_line(stale).items():
            keep = _kept_aliases(group)
            line = self.lines[lineno - 1]
            text = _restate(line, group[0].node, keep) if keep else None
            edits.append(LineEdit(self.cls.path, lineno, text))
        return edits

    def _rewrites(self) -> list[LineEdit] | None:
        edits: list[LineEdit] = []
        for module in self._importers():
            found = self._rewrite(module)
            if found is None:
                return None
            edits.extend(found)
        return edits

    def _importers(self) -> Iterator[ModuleSyntax]:
        for module in (*self.ctx.syntax.sources(), *self.ctx.syntax.tests()):
            if module.path not in (self.cls.path, self.target_path):
                yield module

    def _rewrite(self, module: ModuleSyntax) -> list[LineEdit] | None:
        """Point every import of the class at its new home, or None if one is dynamic."""
        if not self._only_imported(module, self.ctx.files.read_text(module.path)):
            return None
        lines = self.ctx.files.lines(module.path)
        edits = []
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.ImportFrom) or not self._from_source(
                module, node
            ):
                continue
            if any(alias.name == "*" for alias in node.names):
                return None
            alias = next((a for a in node.names if a.name == self.cls.name), None)
            if alias is None:
                continue
            edit = self._rewrite_import(module, node, alias, lines)
            if edit is None:
                return None
            edits.append(edit)
        return edits

    def _from_source(self, module: ModuleSyntax, node: ast.ImportFrom) -> bool:
        base = resolve_relative(
            module.module,
            is_package=module.is_package,
            level=node.level,
            target=node.module,
        )
        return base == self.source.module

    def _rewrite_import(
        self,
        module: ModuleSyntax,
        node: ast.ImportFrom,
        alias: ast.alias,
        lines: list[str],
    ) -> LineEdit | None:
        line = lines[node.lineno - 1]
        if node.lineno != node.end_lineno or "#" in line:
            return None
        indent = line[: len(line) - len(line.lstrip())]
        bound = f" as {alias.asname}" if alias.asname else ""
        moved = f"{indent}from {self.target_module} import {self.cls.name}{bound}"
        others = [a for a in node.names if a is not alias]
        if not others:
            return LineEdit(module.path, node.lineno, moved)
        kept = ", ".join(
            f"{a.name} as {a.asname}" if a.asname else a.name for a in others
        )
        origin = "." * node.level + (node.module or "")
        return LineEdit(
            module.path,
            node.lineno,
            f"{indent}from {origin} import {kept}\n{moved}",
        )

    def _only_imported(self, module: ModuleSyntax, text: str) -> bool:
        """False when the class is reached through an attribute or a string."""
        if f"{self.source.module}.{self.cls.name}" in text:
            return False
        return not any(
            re.search(rf"\b{re.escape(name)}\.{self.cls.name}\b", text)
            for name, bound in module.bindings.items()
            if bound == self.source.module
        )


def _alias_text(alias: ast.alias) -> str:
    return f"{alias.name} as {alias.asname}" if alias.asname else alias.name


def _is_type_checking(node: ast.If) -> bool:
    match node.test:
        case ast.Name(id="TYPE_CHECKING") | ast.Attribute(attr="TYPE_CHECKING"):
            return True
        case _:
            return False


def _type_checking_blocks(tree: ast.Module) -> list[ast.If]:
    return [
        statement
        for statement in tree.body
        if isinstance(statement, ast.If) and _is_type_checking(statement)
    ]


def _top_level(tree: ast.Module) -> Iterator[tuple[ast.stmt, bool]]:
    """Every module-level statement, flagged when it sits in an ``if TYPE_CHECKING:``."""
    for statement in tree.body:
        if isinstance(statement, ast.If) and _is_type_checking(statement):
            yield from ((child, True) for child in statement.body)
        else:
            yield statement, False


def _module_imports(source: ModuleSyntax) -> Iterator[tuple[str, _Import]]:
    for statement, type_checking in _top_level(source.tree):
        if not isinstance(statement, ast.Import | ast.ImportFrom):
            continue
        for alias in statement.names:
            if alias.name == "*":
                continue
            plain = isinstance(statement, ast.Import)
            name = alias.asname or (alias.name.split(".")[0] if plain else alias.name)
            yield name, _Import(name, statement, alias, type_checking)


def _present_imports(module: ModuleSyntax) -> set[str]:
    """Every import the module already has, spelled absolutely."""
    found: set[str] = set()
    for statement, _ in _top_level(module.tree):
        if isinstance(statement, ast.Import):
            found.update(f"import {_alias_text(a)}" for a in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            base = resolve_relative(
                module.module,
                is_package=module.is_package,
                level=statement.level,
                target=statement.module,
            )
            found.update(
                f"from {base} import {_alias_text(a)}"
                for a in statement.names
                if a.name != "*"
            )
    return found


def _free_names(node: ast.ClassDef) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _used_outside(
    lines: list[str], span: tuple[int, int], name: str, ignore: Container[int] = ()
) -> bool:
    """Whether ``name`` still appears outside the moved lines."""
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    start, end = span
    return any(
        pattern.search(line)
        for number, line in enumerate(lines, start=1)
        if not (start <= number <= end) and number not in ignore
    )


def _surrounding_blanks(lines: list[str], span: tuple[int, int]) -> set[int]:
    """Blank lines that would pile up once the class is gone."""
    start, end = span
    after: set[int] = set()
    line = end + 1
    while line <= len(lines) and not lines[line - 1].strip():
        after.add(line)
        line += 1
    if line <= len(lines):
        return after
    before: set[int] = set()
    line = start - 1
    while line > 1 and not lines[line - 1].strip():
        before.add(line)
        line -= 1
    return after | before


def _unique(texts: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(texts))


def _new_module(body: list[str], rendered: list[_Rendered]) -> str:
    runtime = _unique(item.text for item in rendered if not item.type_checking)
    typed = _unique(item.text for item in rendered if item.type_checking)
    header = _with_type_checking(runtime) if typed else list(runtime)
    if typed:
        header += ["", "if TYPE_CHECKING:", *(INDENT + text for text in typed)]
    lines = [*header, "", "", *body] if header else list(body)
    return "\n".join(lines) + "\n"


def _extend_module(
    ctx: AnalysisContext,
    module: ModuleSyntax,
    body: list[str],
    rendered: list[_Rendered],
) -> str | None:
    """Add the class and the imports it needs to a module that already exists."""
    present = _present_imports(module)
    runtime: list[str] = []
    typed: list[str] = []
    for item in rendered:
        if item.text in present:
            continue
        if item.name in module.bindings:
            return None
        (typed if item.type_checking else runtime).append(item.text)

    lines = ctx.files.lines(module.path)
    anchor = _import_anchor(module.tree)
    additions: list[tuple[int, list[str]]] = []
    blocks = _type_checking_blocks(module.tree)
    if typed and blocks:
        block = blocks[-1]
        indent = " " * block.body[0].col_offset
        end = max(s.end_lineno or s.lineno for s in block.body)
        additions.append((end, [indent + text for text in typed]))
    elif typed:
        head = [] if "TYPE_CHECKING" in module.bindings else [_TYPING_IMPORT]
        block_lines = ["", "if TYPE_CHECKING:", *(INDENT + text for text in typed)]
        additions.append((anchor, [*head, *_unique(runtime), *block_lines]))
        runtime = []
    if runtime:
        additions.append((anchor, _unique(runtime)))

    while lines and not lines[-1].strip():
        lines.pop()
    for at, extra in sorted(additions, key=lambda item: item[0], reverse=True):
        lines[at:at] = extra
    tail = ["", "", *body] if lines else list(body)
    return "\n".join([*lines, *tail]) + "\n"


def _import_anchor(tree: ast.Module) -> int:
    """The line after which new imports can go."""
    anchor = 0
    for index, statement in enumerate(tree.body):
        if isinstance(statement, ast.Import | ast.ImportFrom):
            anchor = statement.end_lineno or statement.lineno
        elif index == 0 and isinstance(statement, ast.Expr):
            if isinstance(statement.value, ast.Constant):
                anchor = statement.end_lineno or statement.lineno
        elif isinstance(statement, ast.If) and _is_type_checking(statement):
            continue
        else:
            break
    return anchor


def _by_line(imports: list[_Import]) -> dict[int, list[_Import]]:
    grouped: dict[int, list[_Import]] = {}
    for imp in imports:
        grouped.setdefault(imp.node.lineno, []).append(imp)
    return grouped


def _kept_aliases(group: list[_Import]) -> list[ast.alias]:
    stale = {id(imp.alias) for imp in group}
    return [alias for alias in group[0].node.names if id(alias) not in stale]


def _restate(
    line: str, node: ast.Import | ast.ImportFrom, keep: list[ast.alias]
) -> str:
    """The import statement rewritten with only ``keep`` left on it."""
    indent = line[: len(line) - len(line.lstrip())]
    names = ", ".join(_alias_text(alias) for alias in keep)
    if isinstance(node, ast.Import):
        return f"{indent}import {names}"
    origin = "." * node.level + (node.module or "")
    return f"{indent}from {origin} import {names}"


def _with_type_checking(runtime: list[str]) -> list[str]:
    """``runtime`` with TYPE_CHECKING imported, folded into an existing typing import."""
    prefix = "from typing import "
    header = list(runtime)
    for index, text in enumerate(header):
        if not text.startswith(prefix):
            continue
        names = text.removeprefix(prefix).split(", ")
        if "TYPE_CHECKING" not in names:
            header[index] = prefix + ", ".join(["TYPE_CHECKING", *names])
        return header
    return [_TYPING_IMPORT, *header]
