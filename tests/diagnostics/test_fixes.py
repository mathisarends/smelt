from smelt.diagnostics.fixes import (
    Applicability,
    Fix,
    TextEdit,
)


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
