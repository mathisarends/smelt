"""Source edits that rule fixes share: node spans, whole lines and imports."""

import ast
import bisect
import re
from collections.abc import Collection

from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.fixes import TextEdit, TextSpan

type _Import = ast.Import | ast.ImportFrom
type Located = ast.expr | ast.stmt | ast.alias | ast.arg | ast.keyword | ast.type_param

# The module between ``from`` and ``import``; continuation lines are not matched.
_FROM = re.compile(r"from[ \t]+(?P<module>\.*[\w.]*)[ \t]+import\b")


class SourceText:
    """One file's text with AST positions mapped to character offsets."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._starts = [0]
        self._starts.extend(
            index + 1 for index, char in enumerate(text) if char == "\n"
        )

    def segment(self, node: Located) -> str:
        start, end = self.span(node)
        return self.text[start:end]

    def replace(self, node: Located, replacement: str) -> TextEdit:
        return TextEdit.at(self.text, self.span(node), replacement)

    def stands_alone(self, node: ast.stmt) -> bool:
        """Whether a statement fills its lines, with at most a trailing comment."""
        start, end = self.span(node)
        before = self.text[self.line_start(node.lineno) : start]
        after = self.text[end : self.line_end(node.end_lineno or node.lineno)].strip()
        return not before.strip() and (not after or after.startswith("#"))

    def indentation(self, node: ast.stmt) -> str:
        return self.text[self.line_start(node.lineno) : self.span(node).start]

    def span(self, node: Located) -> TextSpan:
        start = self.offset(node.lineno, node.col_offset)
        end = self.offset(node.end_lineno or node.lineno, node.end_col_offset or 0)
        return TextSpan(start, end)

    def offset(self, line: int, column: int) -> int:
        """The character offset of an AST position (1-based line, UTF-8 byte column)."""
        start = self.line_start(line)
        current = self.text[start : self.line_end(line)]
        prefix = current.encode("utf-8")[:column]
        return start + len(prefix.decode("utf-8", errors="ignore"))

    def insert_line(self, offset: int, line: str) -> TextEdit:
        """Insert `line` as a whole line at `offset`, the start of a line."""
        lead = "\n" if offset == len(self.text) and not self._ends_line() else ""
        return self.insert(offset, f"{lead}{line}\n")

    def insert(self, offset: int, insertion: str) -> TextEdit:
        return TextEdit.at(self.text, TextSpan(offset, offset), insertion)

    def line_end_at(self, offset: int) -> int:
        """The end of the line that contains `offset`, its newline included."""
        return self.line_end(self.line_at(offset))

    def block_start(self, node: ast.stmt) -> int:
        """The start of `node`'s first line, with comment lines directly above."""
        start = self.line_start(node.lineno)
        while start > 0:
            previous = self.line_start(self.line_at(start - 1))
            if not self.text[previous:start].lstrip().startswith("#"):
                break
            start = previous
        return start

    def line_at(self, offset: int) -> int:
        """The 1-based line that contains `offset`."""
        return bisect.bisect_right(self._starts, offset)

    def delete_lines(self, node: ast.stmt) -> TextEdit:
        return TextEdit.at(self.text, self.lines(node), "")

    def lines(self, node: ast.stmt) -> TextSpan:
        """The offsets of the whole lines `node` occupies, its newline included."""
        return TextSpan(
            self.line_start(node.lineno), self.line_end(node.end_lineno or node.lineno)
        )

    def line_start(self, line: int) -> int:
        return self._starts[line - 1]

    def line_end(self, line: int) -> int:
        """The offset after the newline that ends `line`, or the end of the text."""
        return self._starts[line] if line < len(self._starts) else len(self.text)

    def _ends_line(self) -> bool:
        return not self.text or self.text.endswith("\n")


def name_used(tree: ast.AST, name: str, *, ignore: Collection[ast.AST] = ()) -> bool:
    """Whether `name` is read anywhere outside `ignore`, also inside strings.

    Strings count so that quoted annotations, ``__all__`` and docstrings keep an
    import alive; that errs on the side of leaving an unused import behind.
    """
    skipped = {id(node) for root in ignore for node in tree_nodes(root)}
    word = re.compile(rf"\b{re.escape(name)}\b")
    for node in tree_nodes(tree):
        if id(node) in skipped:
            continue
        match node:
            case ast.Name(id=found) if found == name:
                return True
            case ast.Constant(value=str() as value) if word.search(value):
                return True
            case ast.Global(names=names) | ast.Nonlocal(names=names) if name in names:
                return True
            case _:
                pass
    return False


def top_level_import(tree: ast.Module, local: str) -> tuple[_Import, ast.alias] | None:
    """The module-level import statement and alias that bind `local`."""
    for statement in tree.body:
        if not isinstance(statement, ast.Import | ast.ImportFrom):
            continue
        for alias in statement.names:
            if (alias.asname or alias.name.split(".")[0]) == local:
                return statement, alias
    return None


def add_from_import(
    source: SourceText, tree: ast.Module, *, module: str, name: str
) -> TextEdit:
    """Extend a module-level ``from module import ...`` or add one after the imports."""
    for statement in tree.body:
        if (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module == module
            and statement.names[0].name != "*"
        ):
            return source.insert(source.span(statement.names[-1]).end, f", {name}")
    offset = (
        _future_insertion(source, tree)
        if module == "__future__"
        else import_insertion(source, tree)
    )
    following = source.text[offset:].partition("\n")[0]
    # A blank line separates the new import from code that follows directly.
    gap = (
        "\n"
        if following.strip() and not following.startswith(("import ", "from "))
        else ""
    )
    return source.insert_line(offset, f"from {module} import {name}{gap}")


def remove_import(
    source: SourceText, statement: _Import, alias: ast.alias
) -> TextEdit | None:
    """Drop one alias, or the whole statement when it is the last one."""
    return remove_aliases(source, statement, [alias])


def remove_aliases(
    source: SourceText, statement: _Import, aliases: Collection[ast.alias]
) -> TextEdit | None:
    """Drop `aliases`, or the whole statement when none remain.

    Only statements that fill their lines are removed whole, so no block is left
    empty. Comments between aliases would be lost, so they leave the import.
    """
    kept = [alias for alias in statement.names if alias not in aliases]
    if not kept:
        return (
            source.delete_lines(statement) if source.stands_alone(statement) else None
        )
    start = source.span(statement.names[0]).start
    end = source.span(statement.names[-1]).end
    if "#" in source.text[start:end]:
        return None
    if len(kept) == len(statement.names):
        return None
    listed = ", ".join(source.segment(alias) for alias in kept)
    return TextEdit.at(source.text, TextSpan(start, end), listed)


def import_insertion(source: SourceText, tree: ast.Module) -> int:
    """Where a new import line goes: after the leading imports or the docstring."""
    anchor: ast.stmt | None = None
    for index, statement in enumerate(tree.body):
        if (index == 0 and is_docstring(statement)) or isinstance(
            statement, ast.Import | ast.ImportFrom
        ):
            anchor = statement
        elif not _guarded_imports(statement):
            break
    if anchor is not None:
        return source.line_end(anchor.end_lineno or anchor.lineno)
    return source.line_start(tree.body[0].lineno) if tree.body else len(source.text)


def _future_insertion(source: SourceText, tree: ast.Module) -> int:
    """Where a future import goes: directly after the docstring, before any code."""
    if tree.body and is_docstring(tree.body[0]):
        first = tree.body[0]
        return source.line_end(first.end_lineno or first.lineno)
    return source.line_start(tree.body[0].lineno) if tree.body else 0


def is_docstring(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and (
        isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _guarded_imports(statement: ast.stmt) -> bool:
    match statement:
        case ast.If(body=body, orelse=otherwise):
            return all(_guarded_imports(node) for node in (*body, *otherwise))
        case ast.Try(body=body, handlers=handlers, orelse=otherwise, finalbody=final):
            return all(
                _guarded_imports(node) for node in (*body, *otherwise, *final)
            ) and all(
                _guarded_imports(node) or isinstance(node, ast.Pass)
                for handler in handlers
                for node in handler.body
            )
        case ast.Import() | ast.ImportFrom():
            return True
        case _:
            return False


def rewrite_from_module(
    source: SourceText, start: int, *, current: str, spelling: str
) -> TextEdit | None:
    """Replace the module of the from-import at `start`, spelled `current`."""
    match = _FROM.match(source.text, start)
    if match is None or match.group("module") != current:
        return None
    return TextEdit.at(source.text, TextSpan(*match.span("module")), spelling)
