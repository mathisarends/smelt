import ast

import pytest

from smelt.diagnostics.fixes import TextEdit
from smelt.rules.edits import (
    SourceText,
    add_from_import,
    name_used,
    remove_aliases,
    rewrite_from_module,
    top_level_import,
)


def _apply(text: str, edit: TextEdit | None) -> str:
    assert edit is not None
    source = SourceText(text)
    start = source.offset(edit.line, edit.column - 1)
    end = source.offset(edit.end_line, edit.end_column - 1)
    assert text[start:end] == edit.original
    return text[:start] + edit.replacement + text[end:]


def _added(text: str, *, module: str, name: str) -> str:
    source = SourceText(text)
    edit = add_from_import(source, ast.parse(text), module=module, name=name)
    return _apply(text, edit)


def _removed(text: str, *names: str) -> str | None:
    source = SourceText(text)
    statement = next(
        node
        for node in ast.parse(text).body
        if isinstance(node, ast.Import | ast.ImportFrom)
    )
    aliases = [alias for alias in statement.names if alias.name in names]
    edit = remove_aliases(source, statement, aliases)
    return None if edit is None else _apply(text, edit)


def test_spans_count_utf8_byte_columns_as_characters() -> None:
    text = 'label = "äöü"; value = 1\n'
    value = ast.parse(text).body[1]

    assert SourceText(text).segment(value) == "value = 1"


def test_block_start_includes_comment_lines_directly_above() -> None:
    text = "x = 1\n\n# explains f\n# more\ndef f():\n    pass\n"
    function = ast.parse(text).body[1]

    assert text[SourceText(text).block_start(function) :].startswith("# explains f")


def test_statement_with_code_before_it_does_not_stand_alone() -> None:
    text = "x = 1; y = 2  # note\n"
    first, second = ast.parse(text).body

    assert not SourceText(text).stands_alone(second)
    assert not SourceText(text).stands_alone(first)
    assert SourceText("y = 2  # note\n").stands_alone(ast.parse("y = 2\n").body[0])


def test_strings_keep_a_name_in_use() -> None:
    tree = ast.parse('import Path\n\nvalue: "Path"\n')

    assert name_used(tree, "Path", ignore=[tree.body[0]])
    assert not name_used(tree, "Other")


def test_top_level_import_finds_the_alias_that_binds_a_local_name() -> None:
    tree = ast.parse("import os.path\nfrom typing import Any as A\n")

    assert top_level_import(tree, "os") is not None
    found = top_level_import(tree, "A")
    assert found is not None
    assert found[1].name == "Any"
    assert top_level_import(tree, "Any") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "from typing import Any\n\nx = 1\n",
            "from typing import Any, Final\n\nx = 1\n",
        ),
        ('"""Doc."""\nx = 1\n', '"""Doc."""\nfrom typing import Final\n\nx = 1\n'),
        ("import os\n", "import os\nfrom typing import Final\n"),
        ("import os", "import os\nfrom typing import Final\n"),
        ("", "from typing import Final\n"),
    ],
    ids=["extends", "after-docstring", "after-imports", "no-newline", "empty"],
)
def test_add_from_import(text: str, expected: str) -> None:
    assert _added(text, module="typing", name="Final") == expected


def test_future_import_goes_before_other_imports() -> None:
    text = '"""Doc."""\nimport os\n'

    assert _added(text, module="__future__", name="annotations") == (
        '"""Doc."""\nfrom __future__ import annotations\nimport os\n'
    )


def test_remove_aliases_keeps_the_others_or_drops_the_statement() -> None:
    assert _removed("from a import b, c, d\n", "c") == "from a import b, d\n"
    assert _removed("from a import b\nx = 1\n", "b") == "x = 1\n"
    assert _removed("import a, b\n", "a") == "import b\n"


def test_remove_aliases_refuses_to_lose_comments_or_empty_a_block() -> None:
    commented = "from a import (\n    b,  # keep\n    c,\n)\n"

    assert _removed(commented, "c") is None
    assert _removed("x = 1; from a import b\n", "b") is None


def test_rewrite_from_module_replaces_only_the_expected_spelling() -> None:
    text = "from app.storage.models import Record\n"
    source = SourceText(text)

    edit = rewrite_from_module(
        source, 0, current="app.storage.models", spelling=".models"
    )

    assert _apply(text, edit) == "from .models import Record\n"
    assert rewrite_from_module(source, 0, current="app", spelling=".") is None
