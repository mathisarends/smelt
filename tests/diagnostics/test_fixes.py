from smelt.diagnostics.fixes import Applicability, Fix, TextEdit, source_offset


def test_edit_between_texts_covers_only_the_change() -> None:
    edit = TextEdit.between("x = a or b\n", "x = (a if a is not None else (b))\n")

    assert (edit.line, edit.column, edit.end_line, edit.end_column) == (1, 5, 1, 11)
    assert edit.original == "a or b"
    assert edit.replacement == "(a if a is not None else (b))"


def test_span_widens_the_edit_to_the_whole_expression() -> None:
    before = "x = a or (b)\n"
    after = "x = (a if a is not None else ((b)))\n"

    assert TextEdit.between(before, after).original == "a or (b"
    edit = TextEdit.between(before, after, span=(4, 12))

    assert edit.original == "a or (b)"
    assert edit.replacement == "(a if a is not None else ((b)))"


def test_whole_lines_edit_spans_complete_statements() -> None:
    before = "def b(): pass\ndef a(): pass\nz = 1\n"
    after = "def a(): pass\ndef b(): pass\nz = 1\n"

    edit = TextEdit.between(before, after, whole_lines=True)

    assert (edit.line, edit.column, edit.end_line, edit.end_column) == (1, 1, 3, 1)
    assert edit.original == "def b(): pass\ndef a(): pass\n"
    assert edit.replacement == "def a(): pass\ndef b(): pass\n"


def test_columns_count_utf8_bytes_like_findings() -> None:
    text = "é = ä or 1\n"

    edit = TextEdit.between(text, "é = (ä if ä is not None else (1))\n", span=(4, 10))

    assert (edit.column, edit.end_column) == (6, 13)
    assert source_offset(text, 1, 5) == 4


def test_fix_serializes_its_edits() -> None:
    fix = Fix(
        Applicability.SAFE,
        "Reorder",
        (TextEdit(1, 1, 1, 2, "a", "b"),),
    )

    assert fix.to_json() == {
        "applicability": "safe",
        "description": "Reorder",
        "edits": [
            {
                "line": 1,
                "column": 1,
                "end_line": 1,
                "end_column": 2,
                "original": "a",
                "replacement": "b",
            }
        ],
    }
